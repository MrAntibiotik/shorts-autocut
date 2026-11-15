"""Запис реплеїв: гра відтворює реплей, OBS записує екран.

Для кожного реплею:
  1. запускаємо клієнт гри з файлом реплею (WorldOfTanks.exe <файл>.wotreplay) — гра сама його відтворює;
  2. через obs-websocket (OBS 28+: Інструменти → Налаштування WebSocket-сервера) вмикаємо запис;
  3. чекаємо: завантаження гри + тривалість бою з реплею + запас;
  4. зупиняємо запис, закриваємо гру — і так з наступним реплеєм.

Зайвий початок/кінець запису (ангар, завантаження, результати) не страшний: автонарізка сама знаходить
межі бою за таймером. Гра має бути англійською, звук — стандартний голос екіпажу, музика — вимкнена.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from autocut.eu.replay_meta import ReplayInfo, parse_version

log = logging.getLogger("replays")

WIN = sys.platform == "win32"
GAME_EXES = ["win64/WorldOfTanks.exe", "WorldOfTanks.exe"]
PROCESS_NAMES = ["WorldOfTanks.exe"]


class RecordError(Exception):
    pass


class Cancelled(Exception):
    pass


@dataclass
class RecordOptions:
    game_dir: Path
    obs_host: str = "localhost"
    obs_port: int = 4455
    obs_password: str = ""
    obs_exe: str = r"C:\Program Files\obs-studio\bin\64bit\obs64.exe"   # якщо OBS не відкритий — запустити
    obs_profile: str = ""         # профіль OBS для реплеїв (порожньо — поточний)
    obs_collection: str = ""      # набір сцен OBS для реплеїв (порожньо — поточний)
    record_dir: str = ""          # куди OBS пише записи реплеїв; порожньо — «wot replays eu» на робочому столі
    obs_start_wait_s: float = 90.0   # перший запуск OBS буває довгим
    load_s: float = 60.0          # завантаження гри й реплею до початку відліку
    # ОДИН клік лівою кнопкою в грі — камера від обличчя гравця (кожен наступний клік перемикає на вільну
    # камеру і назад, тому клік один). Секунди від запуску гри; «load+15» — через 15 с після завантаження.
    click_at: tuple = ("load-5",)   # 55 с від запуску гри
    tail_s: float = 20.0          # після кінця бою (запасний варіант, якщо кінець не розпізнано)
    detect_end: bool = True       # бачити кінець бою на екрані (знімок з OBS + розпізнавання тексту)
    check_camera: bool = True     # після кліку прочитати напис унизу: поки немає «Fixed camera activated» — клікати ще
    max_camera_clicks: int = 8
    end_tail_s: float = 4.0       # скільки ще писати після «Перемога» / «Відтворення… закінчено»
    default_battle_s: float = 900  # якщо тривалість у реплеї невідома (бій не дограно)
    max_s: float = 1500


STEAM_ROOTS = [r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"]
KNOWN_DIRS = [r"C:\Games\World_of_Tanks_EU", r"C:\Games\World_of_Tanks", r"C:\Games\World_of_Tanks_NA",
              r"D:\Games\World_of_Tanks_EU", r"D:\Games\World_of_Tanks", r"C:\Wargaming.net\World_of_Tanks_EU"]


def is_game_dir(d) -> bool:
    try:
        return bool(d) and any((Path(d) / x).is_file() for x in ["version.xml", *GAME_EXES])
    except OSError:
        return False


def steam_dirs(roots=None) -> list[Path]:
    """«World of Tanks» у всіх бібліотеках Steam (libraryfolders.vdf), на будь-якому диску."""
    out = []
    for root in roots or STEAM_ROOTS:
        libs = [Path(root)]
        vdf = Path(root) / "steamapps" / "libraryfolders.vdf"
        try:
            libs += [Path(m.replace("\\\\", "\\")) for m in
                     re.findall(r'"path"\s+"([^"]+)"', vdf.read_text(encoding="utf-8", errors="replace"))]
        except OSError:
            pass
        out += [lib / "steamapps" / "common" / "World of Tanks" for lib in libs]
    return out


REGION_DIRS = ["eu", "na", "asia", "ru", "ct"]   # Steam-версія: гра — у підпапці регіону


def _game_in(d) -> Path | None:
    """Гра в самій папці або в підпапці регіону (Steam: …\\World of Tanks\\eu, …\\ru)."""
    if not str(d or "").strip():
        return None
    d = Path(d)
    if is_game_dir(d):
        return d
    try:
        subs = [x for x in d.iterdir() if x.is_dir()]
    except OSError:
        return None
    order = {r: i for i, r in enumerate(REGION_DIRS)}
    for sub in sorted(subs, key=lambda x: (order.get(x.name.lower(), 99), x.name.lower())):
        if is_game_dir(sub):
            return sub
    return None


def find_game_dir(preferred="") -> Path:
    """Папка гри: вказана у вкладці → Steam (усі бібліотеки) → стандартні папки Wargaming Game Center;
    у кожній — і підпапки регіонів (eu, na, asia, ru…). Нічого не знайдено — повертає вказану."""
    cands = [preferred] if str(preferred or "").strip() else []
    cands += steam_dirs() + KNOWN_DIRS
    for c in cands:
        found = _game_in(c)
        if found:
            return found
    return Path(preferred or KNOWN_DIRS[0])


def game_exe(game_dir: Path) -> Path:
    game_dir = find_game_dir(game_dir)
    for rel in GAME_EXES:
        p = Path(game_dir) / rel
        if p.exists():
            return p
    raise RecordError(f"Не знайдено WorldOfTanks.exe (шукав у {game_dir}, у Steam і стандартних папках) — "
                      "вкажіть у вкладці «🎮 Реплеї» папку, куди встановлено гру, напр. "
                      "C:\\Program Files (x86)\\Steam\\steamapps\\common\\World of Tanks")


def client_version(game_dir: Path) -> tuple:
    """Версія встановленої гри з version.xml: «v.1.26.0.1 #1234» → (1, 26, 0, 1)."""
    game_dir = find_game_dir(game_dir)
    try:
        text = (Path(game_dir) / "version.xml").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ()
    m = re.search(r"<version>\s*([^<]+)</version>", text)
    return parse_version(m.group(1))[1] if m else ()


END_WORDS = ("відтворення запису", "запису бою закінчено", "перемога", "поразка", "нічия",
             "victory", "defeat", "draw", "replay playback", "playback has", "победа", "поражение", "ничья")


# Ліва кнопка в реплеї перемикає камеру: вільний огляд ↔ за напрямком гравця («фіксація камери»).
# Точний напис залежить від мови й версії клієнта — шукаємо корені слів і стан (увімкнено / вимкнено).
CAM_FREE = ("вільн", "свобод", "free")                          # вільна камера
CAM_LOCK = ("фікс", "фиксир", "закріп", "прив'яз", "привяз", "fixed", "fix cam", "lock", "слідк", "слеж",
            "follow")   # прив'язка до гравця: «Fixed camera activated.»
CAM_PLAYER = ("першої особи", "першого лиця", "первого лица", "first person", "first-person",
              "від гравця", "камера гравця", "player camera", "від обличчя")
CAM_ON = ("увімкн", "включ", "активн", " on", "enabled", "activated")
CAM_OFF = ("вимкн", "выключ", "відключ", "отключ", " off", "disabled", "deactivated")


CAM_SHOT_W = 2560   # знімок для напису про камеру — більший, бо напис дрібний (на 3440×1440 при 1280 не читається)


def camera_state(text: str) -> str | None:
    """«free» — вільна камера, «player» — за гравцем, None — напису про камеру немає."""
    t = f" {(text or '').lower()} "
    off = any(w in t for w in CAM_OFF)
    t_on = t
    for w in CAM_OFF:                       # «deactivated» містить «activated», «выключ» — «включ»
        t_on = t_on.replace(w, " ")
    on = any(w in t_on for w in CAM_ON)
    if any(w in t for w in CAM_PLAYER):
        return "free" if off and not on else "player"
    if any(w in t for w in CAM_FREE):
        return "player" if off and not on else "free"
    if any(w in t for w in CAM_LOCK):
        return "free" if off and not on else "player"
    return None


def green_text(img) -> bool:
    """Яскраво-зелений рядок тексту внизу по центру (над панеллю снарядів) — так гра пише, що камеру
    прив'язано до гравця. Трава/кущі сюди не потрапляють: вони темніші й жовтуватіші (R близько до G)."""
    if img is None:
        return False
    try:
        import numpy as np
        h, w = img.shape[:2]
        crop = np.asarray(img[int(h * 0.55):int(h * 0.92), int(w * 0.2):int(w * 0.8)]).astype(int)
        b, g, r = crop[..., 0], crop[..., 1], crop[..., 2]          # кадр з OBS — у BGR
        # «Fixed camera activated.» — приблизно RGB(113,181,47): зелений канал 160–195, синій низький.
        # Трава/кущі жовтіші (R близько до G) і темніші — їх відсікає різниця G-R.
        mask = (g >= 140) & (g - r >= 45) & (g - b >= 80)
        if mask.sum() < max(40, mask.size * 0.0004):
            return False
        rows = np.where(mask.sum(axis=1) >= 3)[0]
        cols = np.where(mask.any(axis=0))[0]
        if not len(rows) or not len(cols):
            return False
        band, span = rows[-1] - rows[0] + 1, cols[-1] - cols[0] + 1
        return band <= h * 0.09 and span >= w * 0.06 and span > band * 2   # саме рядок тексту, а не пляма
    except Exception:  # noqa: BLE001
        return False


class EndDetector:
    """Кінець бою на екрані: «ПЕРЕМОГА!» / «Поразка» / «Нічия» або вікно «Відтворення запису бою закінчено»."""

    def __init__(self):
        self.reader = None
        self.failed = False

    def text(self, img) -> str:
        if img is None or self.failed:
            return ""
        try:
            if self.reader is None:
                import easyocr
                try:
                    self.reader = easyocr.Reader(["uk", "en"], gpu=True, verbose=False)
                except Exception:  # noqa: BLE001 — без відеокарти
                    self.reader = easyocr.Reader(["uk", "en"], gpu=False, verbose=False)
            return " ".join(t for _, t, _ in self.reader.readtext(img)).lower()
        except Exception as e:  # noqa: BLE001
            log.warning("Розпізнавання кінця бою недоступне (%s) — зупинка за часом", e)
            self.failed = True
            return ""

    def camera(self, img, bottom: float = 0.4) -> str | None:
        """Напис унизу екрана після кліку («Fixed camera activated.» — зелений над панеллю снарядів):
        «free» — вільна камера, «player» — зафіксована за гравцем, None — напису немає."""
        if img is None:
            return None
        try:   # центр знизу: там напис; менша ділянка — точніше розпізнавання дрібного тексту
            h, w = img.shape[:2]
            crop = img[int(h * (1 - bottom)):int(h * 0.97), int(w * 0.25):int(w * 0.75)]
        except Exception:  # noqa: BLE001
            crop = img
        self.last_text = self.text(crop)
        state = camera_state(self.last_text)
        if state is None and green_text(img) and not getattr(self, "green_before", False):
            state = "player"                     # слів не впізнали, але з'явився зелений напис — значить від гравця
            self.last_text = (self.last_text + " (зелений напис)").strip()
        return state

    def ended(self, img) -> str | None:
        t = self.text(img)
        return next((w for w in END_WORDS if w in t), None)


def record_seconds(info: ReplayInfo | None, opts: RecordOptions) -> float:
    battle = info.duration_s if info and info.duration_s else opts.default_battle_s
    # +30 с — відлік перед боєм, який реплей теж показує
    return min(opts.max_s, opts.load_s + 30 + battle + opts.tail_s)


class Obs:
    """Тонка обгортка над obs-websocket v5 (пакет obsws-python)."""

    launched = False                  # OBS запустила програма (а не ви)

    def __init__(self, host: str, port: int, password: str = ""):
        self.prev: tuple[str, str] = ("", "")
        self.prev_dir = ""
        try:
            import obsws_python as obs
        except ImportError as e:
            raise RecordError("Не встановлено пакет obsws-python: запустіть extra\\update.bat "
                              "або pip install obsws-python") from e
        try:
            self.cl = obs.ReqClient(host=host, port=port, password=password or "", timeout=10)
        except Exception as e:  # noqa: BLE001 — бібліотека кидає різне
            raise RecordError(f"Немає зв'язку з OBS ({host}:{port}): {e}. Відкрийте OBS і ввімкніть "
                              "Інструменти → Налаштування WebSocket-сервера (порт і пароль — як у вкладці).") from e

    def recording(self) -> bool:
        return bool(getattr(self.cl.get_record_status(), "output_active", False))

    def screenshot(self, width: int = 1280):
        """Знімок поточної сцени OBS (numpy BGR) або None."""
        try:
            import base64

            import cv2
            import numpy as np
            sc = self.cl.get_current_program_scene()
            name = getattr(sc, "current_program_scene_name", None) or getattr(sc, "scene_name", None)
            try:                                              # пропорції кадру OBS (широкий монітор — не стискати текст)
                vs = self.cl.get_video_settings()
                bw, bh = int(getattr(vs, "base_width", 0) or 0), int(getattr(vs, "base_height", 0) or 0)
                height = max(8, round(width * bh / bw)) if bw and bh else round(width * 9 / 16)
            except Exception:  # noqa: BLE001
                height = round(width * 9 / 16)
            r = self.cl.get_source_screenshot(name, "jpg", width, height, 70)
            data = str(getattr(r, "image_data", "") or "").split(",", 1)[-1]
            return cv2.imdecode(np.frombuffer(base64.b64decode(data), np.uint8), cv2.IMREAD_COLOR)
        except Exception as e:  # noqa: BLE001
            log.debug("Знімок OBS: %s", e)
            return None

    def status(self) -> tuple[bool, str]:
        """(чи йде запис, лічильник OBS «00:03:12»)."""
        r = self.cl.get_record_status()
        tc = str(getattr(r, "output_timecode", "") or "").split(".")[0]
        return bool(getattr(r, "output_active", False)), tc

    def start(self, wait_s: float = 10.0) -> None:
        """Починає запис і ПЕРЕВІРЯЄ, що OBS справді пише (інакше — зрозуміла помилка)."""
        if self.recording():
            raise RecordError("OBS уже записує — зупиніть запис вручну і спробуйте ще раз")
        try:
            self.cl.start_record()
        except Exception as e:  # noqa: BLE001
            raise RecordError(f"OBS відмовився починати запис: {e}") from e
        deadline = time.time() + wait_s
        while time.time() < deadline:
            if self.recording():
                log.info("OBS: запис іде")
                return
            time.sleep(0.5)
        raise RecordError("OBS не почав запис. В OBS перевірте: Налаштування → Вивід → Запис — «Шлях запису» "
                          "(папка існує) і формат (mkv/mp4); спробуйте натиснути «Почати запис» в OBS вручну — "
                          "OBS покаже, що не так. Якщо вказано профіль OBS у вкладці — перевірте саме його.")

    def stop(self) -> str:
        r = self.cl.stop_record()
        return str(getattr(r, "output_path", "") or "")

    def switch(self, profile: str = "", collection: str = "") -> None:
        """Профіль і набір сцен для реплеїв (запам'ятовує поточні, щоб повернути)."""
        try:
            cur_p = getattr(self.cl.get_profile_list(), "current_profile_name", "") or ""
            cur_c = getattr(self.cl.get_scene_collection_list(), "current_scene_collection_name", "") or ""
        except Exception as e:  # noqa: BLE001
            log.warning("OBS: не вдалося прочитати профіль/набір сцен: %s", e)
            return
        self.prev = (cur_p, cur_c)
        try:
            if collection and collection != cur_c:
                self.cl.set_current_scene_collection(collection)
                log.info("OBS: набір сцен «%s»", collection)
                time.sleep(2)
            if profile and profile != cur_p:
                self.cl.set_current_profile(profile)
                log.info("OBS: профіль «%s»", profile)
                time.sleep(2)
        except Exception as e:  # noqa: BLE001
            raise RecordError(f"OBS не перемкнувся на профіль «{profile}» / набір сцен «{collection}»: {e}. "
                              "Перевірте назви (Профіль / Набір сцен у меню OBS).") from e

    def set_dir(self, folder: str) -> None:
        """Папка записів OBS на час запису реплеїв (попередню restore() повертає)."""
        if not folder:
            return
        Path(folder).mkdir(parents=True, exist_ok=True)
        try:
            self.prev_dir = str(getattr(self.cl.get_record_directory(), "record_directory", "") or "")
            if os.path.normcase(os.path.abspath(self.prev_dir or ".")) != os.path.normcase(os.path.abspath(folder)):
                self.cl.set_record_directory(str(folder))
                log.info("OBS: записи реплеїв — у %s", folder)
        except Exception as e:  # noqa: BLE001 — старий OBS (до 30) не вміє — пише в свою папку
            self.prev_dir = ""
            log.warning("OBS не змінив папку записів (%s) — запис піде в папку з налаштувань OBS. "
                        "Оновіть OBS до 30+ або вкажіть папку в OBS: Налаштування → Вивід → Шлях запису.", e)

    def restore(self) -> None:
        """Повертає профіль, набір сцен і папку записів, які були до запису реплеїв."""
        if getattr(self, "prev_dir", ""):
            try:
                self.cl.set_record_directory(self.prev_dir)
            except Exception as e:  # noqa: BLE001
                log.warning("OBS: не вдалося повернути папку записів: %s", e)
        p, c = self.prev
        try:
            cur_p = getattr(self.cl.get_profile_list(), "current_profile_name", "") or ""
            cur_c = getattr(self.cl.get_scene_collection_list(), "current_scene_collection_name", "") or ""
            if p and p != cur_p:
                self.cl.set_current_profile(p)
            if c and c != cur_c:
                self.cl.set_current_scene_collection(c)
        except Exception as e:  # noqa: BLE001
            log.warning("OBS: не вдалося повернути профіль/набір сцен: %s", e)


RECORD_FOLDER = "wot replays eu"


def desktop_dir() -> Path:
    """Робочий стіл Windows (у т.ч. перенесений в OneDrive)."""
    if WIN:
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(260)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf) == 0 and buf.value:   # CSIDL_DESKTOPDIRECTORY
                return Path(buf.value)
        except Exception:  # noqa: BLE001
            pass
        for d in (Path(os.environ.get("OneDrive", "")) / "Desktop", Path.home() / "OneDrive" / "Desktop"):
            if str(d).strip() and d.is_dir():
                return d
    return Path.home() / "Desktop"


def default_record_dir() -> Path:
    """Типова папка записів реплеїв: «wot replays eu» на робочому столі."""
    return desktop_dir() / RECORD_FOLDER


OBS_PROCESSES = ("obs64.exe", "obs32.exe", "obs.exe")


def obs_running() -> bool:
    """Чи вже працює OBS (будь-яка копія, зокрема згорнута в трей)."""
    try:
        if WIN:
            r = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, errors="replace",
                               timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            names = {line.split('","')[0].strip('"').lower() for line in r.stdout.splitlines() if line}
        else:
            r = subprocess.run(["ps", "-A", "-o", "comm="], capture_output=True, text=True, timeout=20)
            names = {Path(x.strip()).name.lower() for x in r.stdout.splitlines()}
    except (OSError, subprocess.SubprocessError):
        return False
    return any(n in names for n in OBS_PROCESSES)


def launch_obs(opts: RecordOptions) -> bool:
    """Запускає OBS (згорнутим на панель задач, НЕ в трей) з профілем і набором сцен для реплеїв.
    Якщо OBS уже працює — друга копія НЕ запускається (False)."""
    exe = Path(opts.obs_exe or "")
    if not exe.is_file() or obs_running():
        return False
    args = [str(exe), "--disable-shutdown-check"]
    if opts.obs_profile:
        args += ["--profile", opts.obs_profile]
    if opts.obs_collection:
        args += ["--collection", opts.obs_collection]
    kw: dict = {"start_new_session": True}
    if WIN:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 7                                   # SW_SHOWMINNOACTIVE: згорнуто, видно на панелі задач
        kw = {"creationflags": subprocess.DETACHED_PROCESS, "startupinfo": si}
    subprocess.Popen(args, cwd=str(exe.parent), **kw)   # OBS шукає свої файли поруч із exe
    log.info("Запускаю OBS: %s", exe)
    return True


WS_HELP = ("у OBS увімкніть Інструменти → Налаштування WebSocket-сервера (порт і пароль — як у вкладці "
           "«🎮 Реплеї»)")


def ensure_obs(opts: RecordOptions) -> "Obs":
    """Зв'язок з OBS. Якщо OBS закритий — запускає ОДНУ копію й чекає WebSocket; якщо OBS уже відкритий
    (навіть у треї), нову копію не запускає. Перемикає профіль / набір сцен (obs.restore() повертає)."""
    try:
        obs = Obs(opts.obs_host, opts.obs_port, opts.obs_password)
    except RecordError as first:
        if obs_running():
            raise RecordError(f"OBS уже відкритий (можливо, згорнутий у трей), але WebSocket не відповідає — "
                              f"{WS_HELP}. Другу копію OBS не запускаю.") from None
        if not launch_obs(opts):
            raise RecordError(f"{first} Шлях до OBS («{opts.obs_exe}») не знайдено — вкажіть obs64.exe у вкладці "
                              "«🎮 Реплеї», і програма запускатиме OBS сама.") from None
        deadline = time.time() + opts.obs_start_wait_s
        while True:
            time.sleep(3)
            try:
                obs = Obs(opts.obs_host, opts.obs_port, opts.obs_password)
                break
            except RecordError:
                if time.time() > deadline:
                    raise RecordError(f"OBS запустився, але WebSocket не відповідає — {WS_HELP}.") from None
        obs.launched = True
        _OBS_OURS["on"] = True
    obs.switch(opts.obs_profile, opts.obs_collection)
    obs.set_dir(opts.record_dir or str(default_record_dir()))
    return obs


def game_running() -> bool | None:
    """Чи працює гра (WorldOfTanks.exe — будь-який процес, не лише запущений нами). None — невідомо."""
    if not WIN:
        return None
    try:
        r = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, errors="replace",
                           timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    names = {line.split('","')[0].strip('"').lower() for line in r.stdout.splitlines() if line}
    return any(n.lower() in names for n in PROCESS_NAMES)


_OBS_OURS = {"on": False}         # OBS відкрила програма (а не ви) — після запису закриємо


def obs_ours() -> bool:
    return _OBS_OURS["on"]


def close_obs(wait_s: float = 15.0) -> bool:
    """Закриває OBS, який відкрила програма: спершу м'яко (як хрестик вікна), потім — примусово.
    OBS, відкритий вами, не чіпається. True — закрито."""
    if not _OBS_OURS["on"]:
        return False
    _OBS_OURS["on"] = False
    if not WIN:
        return False
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for name in OBS_PROCESSES:
        subprocess.run(["taskkill", "/IM", name], capture_output=True, creationflags=flags)
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if not obs_running():
            log.info("OBS закрито (його відкривала програма)")
            return True
        time.sleep(1)
    for name in OBS_PROCESSES:                      # --disable-shutdown-check: без «безпечного режиму» потім
        subprocess.run(["taskkill", "/IM", name, "/F"], capture_output=True, creationflags=flags)
    log.info("OBS закрито примусово")
    return True


def kill_game() -> None:
    for name in PROCESS_NAMES:
        if WIN:
            subprocess.run(["taskkill", "/IM", name, "/T", "/F"], capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            subprocess.run(["pkill", "-f", name], capture_output=True)


GAME_TITLES = ("WoT Client", "World of Tanks")


def click_times(opts: RecordOptions) -> list[float]:
    """«load», «load+10», «75» → секунди від запуску гри."""
    out = []
    for c in opts.click_at or ():
        t = str(c).replace(" ", "").lower()
        try:
            if t.startswith("load"):
                out.append(float(opts.load_s) + float(t[4:] or 0))
            else:
                out.append(float(t))
        except ValueError:
            log.warning("Неправильний час кліку: %r", c)
    return sorted(out)


def _game_window():
    """Вікно гри (Windows): за назвою «WoT Client» / «World of Tanks»."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    for title in GAME_TITLES:
        h = user32.FindWindowW(None, title)
        if h:
            return h
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        n = user32.GetWindowTextLengthW(hwnd)
        if n and user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if any(t.lower() in buf.value.lower() for t in GAME_TITLES):
                found.append(hwnd)
                return False
        return True
    user32.EnumWindows(each, 0)
    return found[0] if found else None


def click_player_view() -> bool:
    """Ліва кнопка миші в центрі вікна гри — камера переходить на танк гравця. True — клікнули."""
    if not WIN:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        hwnd = _game_window()
        if hwnd:
            user32.ShowWindow(hwnd, 9)               # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            x, y = (r.left + r.right) // 2, (r.top + r.bottom) // 2
        else:
            x, y = user32.GetSystemMetrics(0) // 2, user32.GetSystemMetrics(1) // 2
        time.sleep(0.3)
        user32.SetCursorPos(x, y)
        user32.mouse_event(0x0002, 0, 0, 0, 0)       # ліва кнопка вниз
        time.sleep(0.08)
        user32.mouse_event(0x0004, 0, 0, 0, 0)       # ліва кнопка вгору
        log.info("Клік у грі — камера від обличчя гравця%s", "" if hwnd else " (вікно гри не знайдено — центр екрана)")
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("Клік у грі не вдався: %s", e)
        return False


def record_one(replay: Path, info: ReplayInfo | None, opts: RecordOptions, obs: Obs,
               progress=None, cancel=None, on_camera=None) -> Path:
    """on_camera(стан, текст напису) — що показала гра після кліку (для живої картки)."""
    """Відтворює один реплей і повертає шлях до запису OBS."""
    exe = game_exe(opts.game_dir)
    total = record_seconds(info, opts)
    kill_game()                    # гра з попереднього реплею могла лишитися
    time.sleep(2)
    log.info("Реплей %s: запускаю гру, запис ~%d хв", replay.name, round(total / 60))
    proc = subprocess.Popen([str(exe), str(Path(replay).resolve())], cwd=str(exe.parent))
    launched = time.time()
    clicks = click_times(opts)
    started = False
    try:
        obs.start()
        started = True
        t0 = time.time()
        last_check, obs_tc = t0, ""
        last_game_check, gone = t0, 0
        # кінець бою бачимо на екрані: перевіряємо ближче до розрахованого кінця (або з 90-ї с, якщо тривалість невідома)
        known = bool(info and info.duration_s)
        end_from = max(opts.load_s + 60, total - opts.tail_s - 150) if known else opts.load_s + 90
        detector = EndDetector() if opts.detect_end else None
        cam = EndDetector() if opts.check_camera else None   # напис про камеру внизу екрана після кліку
        cam_checks: list[float] = []
        cam_clicks = 0
        last_end_check, end_at = t0, None
        while time.time() - t0 < total:
            if cancel and cancel():
                raise Cancelled()
            if progress:
                progress(f"Запис {replay.name}: {int(time.time() - t0)} / {int(total)} с"
                         + (f" · OBS ● REC {obs_tc}" if obs_tc else ""), (time.time() - t0) / total)
            if time.time() - t0 > opts.load_s and time.time() - last_game_check >= 5:
                last_game_check = time.time()
                alive = game_running()
                if alive is None:                               # не вдалось дізнатися — як раніше, за процесом
                    alive = proc.poll() is None
                gone = 0 if alive else gone + 1
                if gone >= 2:
                    log.info("Гру закрито — зупиняю запис")
                    break
            if end_at is not None and time.time() >= end_at:
                log.info("Бій закінчився — зупиняю запис")
                break
            if (detector and end_at is None and time.time() - t0 >= end_from
                    and time.time() - last_end_check >= 5):
                last_end_check = time.time()
                word = detector.ended(obs.screenshot())
                if word:
                    log.info("На екрані «%s» — кінець бою, ще %.0f с і зупиняю", word, opts.end_tail_s)
                    end_at = time.time() + opts.end_tail_s
            while clicks and time.time() - launched >= clicks[0]:   # камера від обличчя гравця
                clicks.pop(0)
                if cam and not cam_clicks:                          # зелене було ще до кліку — йому не віримо
                    cam.green_before = green_text(obs.screenshot(width=CAM_SHOT_W))
                click_player_view()
                cam_clicks += 1
                now = time.time()
                cam_checks = [now + 1.5, now + 3.0, now + 4.5, now + 6.0] if cam else []
            if cam_checks and time.time() >= cam_checks[0]:            # який напис з'явився після кліку?
                cam_checks.pop(0)
                state = cam.camera(obs.screenshot(width=CAM_SHOT_W))
                seen = (getattr(cam, "last_text", "") or "").strip()
                if state and on_camera:
                    on_camera(state, seen)
                if state:
                    log.info("Напис про камеру: «%s» → %s", seen[:120], state)
                if state == "player":
                    log.info("Камеру зафіксовано за гравцем — так і лишаю")
                    cam_checks = []
                elif state == "free" or not cam_checks:
                    # вільна камера або «Fixed camera activated» так і не з'явився — клікаємо ще (лівою кнопкою)
                    if cam_clicks < opts.max_camera_clicks:
                        log.info("%s — клікаю ще раз (%d/%d)", "Увімкнулась вільна камера" if state == "free" else
                                 "Напису «Fixed camera activated» немає", cam_clicks + 1, opts.max_camera_clicks)
                        cam.green_before = green_text(obs.screenshot(width=CAM_SHOT_W))
                        click_player_view()
                        cam_clicks += 1
                        now = time.time()
                        cam_checks = [now + 1.0, now + 2.0, now + 3.0, now + 4.5]
                        if on_camera and state == "free":
                            on_camera("free", seen)
                    else:
                        log.warning("Камера не зафіксувалась після %d кліків", cam_clicks)
                        cam_checks = []
                        if on_camera:
                            on_camera(None, seen)
            if time.time() - last_check >= 15:                       # OBS справді пише?
                last_check = time.time()
                try:
                    active, tc = obs.status()
                except Exception as e:  # noqa: BLE001
                    active, tc = True, ""
                    log.debug("OBS статус: %s", e)
                if not active:
                    raise RecordError("OBS зупинив запис посеред бою (закрили OBS, скінчилось місце на диску?)")
                obs_tc = tc
            time.sleep(1)
    finally:
        out = ""
        if started:
            try:
                out = obs.stop()
            except Exception as e:  # noqa: BLE001
                log.warning("OBS не зупинив запис: %s", e)
        kill_game()
    if not out:
        raise RecordError("OBS не повідомив, куди збережено запис (перевірте Налаштування → Вивід → Шлях запису)")
    path = Path(out)
    for _ in range(30):            # OBS дописує файл ще кілька секунд
        if path.exists() and path.stat().st_size > 0:
            break
        time.sleep(1)
    if not path.exists():
        raise RecordError(f"Файл запису не знайдено: {path}")
    return path


__all__ = ["Cancelled", "EndDetector", "green_text", "Obs", "camera_state", "close_obs", "obs_ours", "RecordError", "RecordOptions", "default_record_dir", "desktop_dir", "obs_running", "click_player_view", "click_times", "client_version",
           "ensure_obs", "find_game_dir",
           "game_exe", "kill_game",
           "launch_obs",
           "record_one", "record_seconds"]
