"""Telegram-бот автонарізки: статус обробки, перегляд готових шортсів (480p), «залишити» / «видалити».

Працює на вашому ПК (без сервера): сам питає Telegram про нові повідомлення. Відповідає ЛИШЕ власнику
(Telegram ID збережено в config\\telegram.json при налаштуванні). Налаштування: extra\\setup_telegram.bat.

Команди: /status — що зараз обробляється; /shorts — шортси останнього результату; /all — усі результати;
/kept — залишені; /help. Під кожним шортсом: ▶ 480p, 📱 оригінал, ✅ Залишити, 🗑 Видалити
(видалений шортс переноситься в output\\_видалені — його можна повернути вручну).
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from autocut.core.config import CONFIG_DIR, load_settings, output_dir, work_dir  # noqa: E402
from autocut.core.logs import setup_logging  # noqa: E402
from autocut.core.resolve_export import read_status  # noqa: E402
from autocut.core.util import fmt_time  # noqa: E402
from autocut.bot.chat import REFRESH_AFTER, TTL_BUTTONS, TTL_ERROR, TTL_INFO, ChatRegistry  # noqa: E402
from autocut.bot.access import (CONTROL, OWNER_ACTS, OWNER_CMDS, ROLE_NAMES, VIEWER_CMDS, Access,  # noqa: E402
                                display_name)

log = logging.getLogger("telegram")
CFG_FILE = CONFIG_DIR / "telegram.json"
LOCK_PORT = 47321                   # один бот на ПК
MAX_UPLOAD = 49 * 1024 * 1024       # ліміт Telegram для ботів — 50 МБ
DELETED_DIR = "_видалені"
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


# ====================================================================== налаштування

def load_cfg() -> dict:
    try:
        return json.loads(CFG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_cfg(cfg: dict) -> None:
    CFG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ====================================================================== Telegram API

class TgApi:
    def __init__(self, token: str):
        self.base = f"https://api.telegram.org/bot{token}/"

    def call(self, method: str, _timeout: float = 40, **params):
        data = json.dumps({k: v for k, v in params.items() if v is not None}).encode("utf-8")
        req = urllib.request.Request(self.base + method, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=_timeout) as r:
                res = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            raise RuntimeError(f"Telegram {method}: {e.code} {body[:300]}") from None
        if not res.get("ok"):
            raise RuntimeError(f"Telegram {method}: {res}")
        return res["result"]

    def send_file(self, method: str, field: str, path: Path, _timeout: float = 300, **params):
        """Надсилання файлу (sendVideo / sendDocument) — multipart/form-data."""
        boundary = uuid.uuid4().hex
        parts = []
        for k, v in params.items():
            if v is None:
                continue
            if isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False)
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode("utf-8"))
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{path.name}\"\r\n"
                f"Content-Type: {ctype}\r\n\r\n").encode("utf-8")
        body = b"".join(parts) + head + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode("utf-8")
        req = urllib.request.Request(self.base + method, data=body,
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(req, timeout=_timeout) as r:
                res = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Telegram {method}: {e.code} {e.read().decode('utf-8', 'replace')[:300]}") from None
        if not res.get("ok"):
            raise RuntimeError(f"Telegram {method}: {res}")
        return res["result"]


# ====================================================================== результати обробки

def list_jobs(settings: dict) -> list[Path]:
    out = output_dir(settings)
    return sorted((p for p in out.glob("job_*") if (p / "job.json").exists()), key=lambda p: p.name)


def load_job(job_dir: Path) -> dict | None:
    try:
        return json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    except Exception:
        return None


def job_shorts(job_dir: Path) -> list[dict]:
    """Шортси результату: [{i, name, mp4, dir, duration, deleted}] (i — номер у результаті)."""
    job = load_job(job_dir) or {}
    res = []
    i = 0
    for rec in job.get("recordings", []):
        for sh in rec.get("shorts", []):
            i += 1
            mp4 = Path(sh["mp4"]) if sh.get("mp4") else job_dir / sh.get("dir", "") / f"{sh['file_base']}.mp4"
            bdir = job_dir / sh["dir"] if sh.get("dir") else None
            res.append({"i": i, "name": sh.get("name", f"Шортс {i}"), "mp4": mp4, "dir": bdir,
                        "battle": sh.get("battle"),
                        "duration": float(sh.get("duration") or 0), "deleted": not mp4.exists()})
    return res


def short_key(job_dir: Path, i: int) -> str:
    return f"{job_dir.name.removeprefix('job_')}|{i}"


def find_short(settings: dict, key: str) -> tuple[Path, dict] | None:
    jid, _, i = key.partition("|")
    job_dir = output_dir(settings) / f"job_{jid}"
    for s in job_shorts(job_dir):
        if str(s["i"]) == i:
            return job_dir, s
    return None


class Marks:
    """Шортси «до публікації»: ключ шортсу -> шлях MP4 у папці публікації (output\\_telegram.json)."""

    def __init__(self, settings: dict):
        self.path = output_dir(settings) / "_telegram.json"
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.data = {}
        if not isinstance(self.data.get("published"), dict):
            self.data["published"] = {}

    def deleted(self) -> dict:
        return self.data.setdefault("deleted", {})

    def set_deleted(self, key: str, entry: dict | None):
        if entry is None:
            self.deleted().pop(key, None)
        else:
            self.deleted()[key] = entry
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")

    def published(self, key: str) -> Path | None:
        p = self.data["published"].get(key)
        return Path(p) if p and Path(p).exists() else None

    def set(self, key: str, path: Path | None):
        if path is None:
            self.data["published"].pop(key, None)
        else:
            self.data["published"][key] = str(path)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")


def publish_dir(settings: dict, cfg: dict) -> Path:
    from autocut.core.content import folder
    d = Path(cfg["publish_dir"]) if cfg.get("publish_dir") else folder(settings, "publish")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _unique(dst: Path) -> Path:
    n = 2
    out = dst
    while out.exists():
        out = dst.with_name(f"{dst.stem}_{n}{dst.suffix}")
        n += 1
    return out


def move_with_srt(mp4: Path, dst_dir: Path) -> Path:
    """Переносить MP4 і його субтитри (той самий початок назви, .srt) у dst_dir. Повертає новий шлях MP4."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    new = _unique(dst_dir / mp4.name)
    shutil.move(str(mp4), str(new))
    srt = mp4.with_suffix(".srt")
    if srt.exists():
        shutil.move(str(srt), str(new.with_suffix(".srt")))
    return new


def make_preview(settings: dict, mp4: Path, horizontal: bool = False) -> Path:
    """Легка копія для перегляду в Telegram (кешується): шортс — 480 завширшки, хайлайт — 480 заввишки."""
    from autocut.core.media import find_tool
    d = work_dir(settings) / "tg_preview"
    d.mkdir(parents=True, exist_ok=True)
    st = mp4.stat()
    out = d / f"{mp4.stem}_{int(st.st_mtime)}_{st.st_size}_480.mp4"
    if out.exists():
        return out
    tmp = out.with_suffix(".tmp.mp4")
    try:
        from autocut.core.media import probe
        dur = max(1.0, float(probe(settings, mp4).duration))
    except Exception:
        dur = 60.0
    # якість під ліміт Telegram (50 МБ): бітрейт = ціль / тривалість; не влізло — ще менше (до 4 спроб)
    target_mb, height, abit = 40.0, (480 if horizontal else 0), 96
    for attempt in range(4):
        vbit = max(120, int(target_mb * 1024 * 1024 * 8 / dur / 1000) - abit - 16)   # кбіт/с відео
        if horizontal:
            height = 720 if vbit >= 1800 else 540 if vbit >= 800 else 432 if vbit >= 450 else 360
            vf = f"scale=-2:{height}"
        else:
            vf = "scale=480:-2" if vbit >= 350 else "scale=360:-2"
        cmd = [find_tool(settings, "ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-i", str(mp4),
               "-vf", vf + ",setsar=1", "-c:v", "libx264", "-preset", "medium" if horizontal else "veryfast",
               "-crf", "26" if horizontal else "28",
               "-maxrate", f"{vbit}k", "-bufsize", f"{vbit}k",
               "-c:a", "aac", "-b:a", f"{abit}k", "-movflags", "+faststart", str(tmp)]
        r = subprocess.run(cmd, capture_output=True, creationflags=_NO_WINDOW)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode("utf-8", "replace")[-400:])
        size = tmp.stat().st_size
        if size <= MAX_UPLOAD:
            break
        log.info("Перегляд %s: %.1f МБ — завеликий, стискаю сильніше", mp4.name, size / 1024 / 1024)
        target_mb *= 0.75 * MAX_UPLOAD / size * 1.2      # пропорційно перевищенню, з запасом
        abit = 64
    tmp.replace(out)
    return out


def video_size(settings: dict, mp4: Path) -> tuple[int, int, int | None]:
    """Ширина, висота, тривалість — Telegram без розміру показує відео квадратом (сплющеним)."""
    try:
        from autocut.core.media import probe
        info = probe(settings, mp4)
        return int(info.width), int(info.height), int(info.duration)
    except Exception:
        return 768, 480, None


def delete_short(settings: dict, job_dir: Path, s: dict, cur: Path) -> dict:
    """Переносить шортс у output\\_видалені\\<результат>\\: усю папку бою (якщо шортс на місці)
    або лише MP4 + SRT (якщо він уже в папці публікації). Повертає запис для відновлення."""
    dst_root = output_dir(settings) / DELETED_DIR / job_dir.name
    dst_root.mkdir(parents=True, exist_ok=True)
    if cur == s["mp4"] and s["dir"] and s["dir"].is_dir() and s["dir"] != job_dir:
        dst = _unique(dst_root / s["dir"].name)
        shutil.move(str(s["dir"]), str(dst))
        return {"kind": "dir", "moved": str(dst), "mp4": str(dst / s["mp4"].name), "t": time.time()}
    new = move_with_srt(cur, dst_root)
    return {"kind": "file", "moved": str(new), "mp4": str(new), "t": time.time()}


def restore_short(entry: dict, s: dict) -> Path:
    """Повертає видалений шортс на його місце в результаті. Повертає шлях MP4."""
    if entry["kind"] == "dir":
        target = s["dir"]
        if target.exists():
            raise RuntimeError(f"на місці вже є папка {target.name}")
        shutil.move(entry["moved"], str(target))
        return s["mp4"]
    src = Path(entry["moved"])
    back = move_with_srt(src, s["mp4"].parent)
    if back != s["mp4"] and not s["mp4"].exists():
        shutil.move(str(back), str(s["mp4"]))
        if back.with_suffix(".srt").exists():
            shutil.move(str(back.with_suffix(".srt")), str(s["mp4"].with_suffix(".srt")))
        back = s["mp4"]
    return back


# ====================================================================== бот

class Bot:
    def __init__(self, settings: dict, cfg: dict, api: TgApi | None = None):
        self.settings = settings
        self.cfg = cfg
        self.api = api or TgApi(cfg["token"])
        self.owner = cfg.get("owner_id")
        self.offset = None
        self.last_status: tuple | None = None
        self.lock = None                       # сокет «один бот на ПК» (для перезапуску після оновлення)
        self.notified_sha = cfg.get("notified_sha")
        self.hl_busy = False
        self.rec_choices: dict[str, tuple] = {}   # id кнопки -> (результат, шортс, файл запису)
        self.last_update_check = 0.0
        self._ctx = threading.local()          # у чиєму чаті зараз дія (власник / адмін / глядач)
        self._chats: dict[int, ChatRegistry] = {}
        self.access = Access(cfg, lambda: save_cfg(self.cfg), lambda: self.owner)
        self._card_last = ("", 0.0)            # (текст картки, коли рахували)
        self._card_bottom = False              # картку треба перенести вниз (під нові шортси)
        try:
            from autocut.eu.replays_bot import ReplaysBot
            self.replays = ReplaysBot(self)       # «🎮 Реплеї»: фільтр wot-record, файли реплеїв, запис
        except Exception as e:  # реплеї не повинні ламати бота
            log.warning("Реплеї в боті недоступні: %s", e)
            self.replays = None

    # -------------------------------------------------------------- кілька людей: чий чат зараз
    @property
    def cid(self):
        """Чат, з яким зараз працюємо: того, хто натиснув/написав; фонові справи — власник."""
        return getattr(self._ctx, "chat", None) or self.owner

    @property
    def chat(self) -> ChatRegistry:
        """Облік повідомлень саме цього чату (у кожної людини — свій)."""
        cid = self.cid
        reg = self._chats.get(cid)
        if reg is None:
            name = "tg_chat.json" if cid == self.owner else f"tg_chat_{cid}.json"
            reg = self._chats[cid] = ChatRegistry(work_dir(self.settings) / name)
        return reg

    def as_chat(self, cid):
        bot = self

        class _Ctx:
            def __enter__(self_):
                self_.prev = getattr(bot._ctx, "chat", None)
                bot._ctx.chat = cid

            def __exit__(self_, *a):
                bot._ctx.chat = self_.prev
        return _Ctx()

    def role(self) -> str | None:
        return self.access.role(self.cid)

    # -------------------------------------------------------------- відправка
    # постійне меню внизу чату
    # Підписи кнопок: що саме робить кожна. Мій канал — без позначки, EU-канал — «🇬🇧 EU».
    MENU = {"keyboard": [[{"text": "📋 Нові шортси на перевірку"}, {"text": "⏳ Що зараз робиться"}],
                         [{"text": "✅ Схвалені (до публікації)"}, {"text": "🗑 Видалені — відновити"}],
                         [{"text": "📁 Усі нарізки"}, {"text": "🔄 Оновити програму"}],
                         [{"text": "🇬🇧 EU: реплеї → шортси"}, {"text": "🧹 Очистити чат"}]],
            "resize_keyboard": True, "is_persistent": True}
    MENU_CMDS = {"📋 Нові шортси на перевірку": "/shorts", "⏳ Що зараз робиться": "/status",
                 "✅ Схвалені (до публікації)": "/kept", "📁 Усі нарізки": "/all", "🔄 Оновити програму": "/update",
                 "🧹 Очистити чат": "/clear", "🗑 Видалені — відновити": "/deleted", "🇬🇧 EU: реплеї → шортси": "/replays",
                 "👥 Доступ: адміністратори й перегляд": "/users",
                 # старі підписи (меню до оновлення) — теж працюють
                 "📋 Шортси": "/shorts", "⏳ Статус": "/status", "📤 До публікації": "/kept",
                 "📁 Усі результати": "/all", "🔄 Оновлення": "/update", "🗑 Видалені": "/deleted", "🎮 Реплеї": "/replays"}

    VIEW_MENU = {"keyboard": [[{"text": "⏳ Що зараз робиться"}, {"text": "🧹 Очистити чат"}]],
                 "resize_keyboard": True, "is_persistent": True}

    def menu(self) -> dict:
        """Нижнє меню за роллю: власнику — ще «👥 Доступ»; глядачу — лише статус і очищення чату."""
        r = self.role()
        if r == "owner":
            return {**self.MENU, "keyboard": self.MENU["keyboard"] + [[{"text": "👥 Доступ: адміністратори й перегляд"}]]}
        return self.MENU if r in CONTROL else self.VIEW_MENU

    def delete_msg(self, mid) -> None:
        if not mid:
            return
        self.chat.forget(mid)
        try:
            self.api.call("deleteMessage", chat_id=self.cid, message_id=mid)
        except Exception:
            pass

    def delete_later(self, mid, delay: float = 6.0) -> None:
        threading.Timer(delay, self.delete_msg, args=(mid,)).start()

    def offer_recordings(self, job_dir: Path, s: dict, err: str):
        """Запис не знайдено — кнопки з найновішими відео з папки стрімів."""
        from autocut.core.config import recordings_dir
        from autocut.core.recording import recent_videos
        vids = recent_videos(self.settings, 6)
        if not vids:
            self.send(f"❌ Хайлайт не вийшов:\n{err}\n\nУ папці стрімів ({recordings_dir(self.settings)}) відео "
                      f"немає. Змініть папку у вікні програми («Папка зі стрімами»).")
            return
        self.rec_choices.clear()
        rows = []
        for i, v in enumerate(vids):
            cid = f"{i}{int(time.time()) % 100000}"
            self.rec_choices[cid] = (job_dir, s, str(v))
            mb = v.stat().st_size / 1024 ** 3
            stamp = time.strftime("%d.%m %H:%M", time.localtime(v.stat().st_mtime))
            rows.append([{"text": f"🎞 {v.name}  ({stamp}, {mb:.1f} ГБ)", "callback_data": f"P|{cid}"}])
        rows.append([{"text": "✖ Скасувати", "callback_data": "X|"}])
        self.send(f"🔎 {err}\n\nЯкий це запис? Ось найновіші з папки стрімів ({recordings_dir(self.settings)}):",
                  markup={"inline_keyboard": rows})

    def start_highlight(self, job_dir: Path, s: dict, rec: str | None = None):
        """Хайлайт цього бою у фоні (tools/make_highlight.py); готовий — надіслати відео 480p."""
        if self.hl_busy:
            self.send("⏳ Уже роблю інший хайлайт — дочекайтесь, будь ласка.")
            return
        if s.get("battle") is None:
            self.send("Для цього шортсу немає даних бою (старий результат).")
            return
        self.hl_busy = True
        wait = self.send(f"⏳ Хайлайт: {s['name']}\nРоблю на 7–8 хв (увесь кадр стріму) з opener'ом — це займе кілька хвилин…")

        chat = self.cid

        def work():
            with self.as_chat(chat):
                return _work()

        def _work():
            try:
                exe = Path(sys.executable)
                pyw = exe.with_name("pythonw.exe")             # вікно прогресу без консолі
                exe = pyw if pyw.exists() else exe
                cmd = [str(exe), str(ROOT / "tools" / "make_highlight.py"), str(job_dir), str(s["battle"]), "--json",
                       "--gui", "--title", s["name"]]
                if rec:
                    cmd += ["--rec", rec]
                r = subprocess.run(cmd, capture_output=True, cwd=str(ROOT), creationflags=_NO_WINDOW)
                out = r.stdout.decode("utf-8", "replace")
                line = next((ln for ln in out.splitlines() if ln.startswith("RESULT ")), None)
                self.delete_msg(wait.get("message_id") if isinstance(wait, dict) else None)
                if r.returncode != 0 or not line:
                    txt = (out + r.stderr.decode("utf-8", "replace")).strip()
                    if "[ПОМИЛКА]" in txt:
                        txt = txt[txt.rindex("[ПОМИЛКА]") + 9:].strip()
                    if "Не знайдено запис гри" in txt and not rec:
                        self.offer_recordings(job_dir, s, txt.splitlines()[0])
                        return
                    self.send(f"❌ Хайлайт не вийшов:\n{txt[-700:] or '?'}")
                    return
                res = json.loads(line[7:])
                mp4 = Path(res["mp4"])
                cap = (f"🎬 Хайлайт: {s['name']}\n⏱ {res['duration'] / 60:.1f} хв"
                       + (" + opener" if res.get("opener") else "") + "\n📁 " + str(mp4.parent))
                if not res.get("opener"):
                    why = res.get("opener_note") or "причина в логах (work\\logs)"
                    cap += ("\n⚠ Без opener'а: " + why)[:700]
                try:
                    prev = make_preview(self.settings, mp4, horizontal=True)
                    if prev.stat().st_size > MAX_UPLOAD:
                        raise RuntimeError("завеликий")
                    self.api.call("sendChatAction", chat_id=self.cid, action="upload_video")
                    vw, vh, vd = video_size(self.settings, prev)     # без розміру Telegram показує квадрат
                    self.track(self.api.send_file("sendVideo", "video", prev, chat_id=self.cid, caption=cap,
                                                  supports_streaming="true", width=vw, height=vh, duration=vd,
                                                  _timeout=900), 30 * 60)   # подивитись — і зникне
                except Exception as e:
                    log.warning("Перегляд хайлайту не надіслано: %s", e)
                    self.send(cap + "\n(перегляд завеликий для Telegram — файл на ПК)", ttl=TTL_BUTTONS)
            except Exception as e:
                log.exception("Хайлайт")
                self.send(f"❌ Хайлайт не вийшов: {e}")
            finally:
                self.hl_busy = False

        threading.Thread(target=work, daemon=True).start()

    def cmd_deleted(self, limit: int = 5):
        marks = Marks(self.settings)
        items = []
        for key, e in marks.deleted().items():
            if Path(e.get("mp4", "")).exists():
                items.append((e.get("t", 0), key, e))
        items.sort(reverse=True)
        if not items:
            res = self.send("🗑 Видалених шортсів немає.")
            self.delete_later(res.get("message_id") if isinstance(res, dict) else None)
            return
        self.send(f"🗑 Останні видалені ({min(limit, len(items))} з {len(items)}) — «♻ Відновити» поверне шортс:")
        for _t, key, e in items[:limit]:
            found = find_short(self.settings, key)
            name = found[1]["name"] if found else Path(e["mp4"]).stem
            markup = {"inline_keyboard": [[{"text": "♻ Відновити", "callback_data": f"R|{key}"}]]}
            try:
                prev = make_preview(self.settings, Path(e["mp4"]))
                res = self.api.send_file("sendVideo", "video", prev, chat_id=self.cid,
                                         caption=f"🗑 Видалено: {name}", supports_streaming="true",
                                         width=480, height=854, reply_markup=markup)
                self.track(res, TTL_BUTTONS)
            except Exception:
                self.send(f"🗑 Видалено: {name}", markup)

    # -------------------------------------------------------------- журнал повідомлень (для «Очистити чат»)
    def _msg_file(self) -> Path:
        return work_dir(self.settings) / "tg_messages.json"

    def track(self, res, ttl: float | None = TTL_INFO) -> None:
        """Запам'ятати повідомлення: ttl — через скільки секунд прибрати (None — постійне)."""
        mid = res.get("message_id") if isinstance(res, dict) else res
        self.chat.add(mid, ttl)

    @staticmethod
    def _auto_ttl(markup: dict | None) -> float:
        rows = (markup or {}).get("inline_keyboard") or []
        return TTL_BUTTONS if any(rows) else TTL_INFO

    def send(self, text: str, markup: dict | None = None, ttl="auto", silent: bool = False):
        """Повідомлення в чат. Усе, крім картки й непідтверджених шортсів, — тимчасове:
        ttl="auto" — 2 хв (з кнопками — 15 хв), число — свої секунди, None — постійне."""
        res = self.api.call("sendMessage", chat_id=self.cid, text=text,
                            reply_markup=markup if markup is not None else self.menu(),
                            disable_web_page_preview=True, disable_notification=silent)
        self.track(res, self._auto_ttl(markup) if ttl == "auto" else ttl)
        return res

    def cmd_clear(self):
        self.send("🧹 Прибрати з чату все, крім картки статусу й непідтверджених шортсів? Файли на ПК не чіпаються.\n\n"
                  "ℹ️ Telegram дозволяє ботам видаляти лише повідомлення за останні 48 год. Старіші (від старих "
                  "версій бота) приберіть один раз самі: меню чату (⋮) → «Очистити історію». Далі бот не лишає "
                  "старих повідомлень: усе тимчасове зникає саме, а картку й шортси він переотправляє вчасно.",
                  {"inline_keyboard": [[{"text": "🧹 Так, очистити", "callback_data": "X|go"},
                                        {"text": "Скасувати", "callback_data": "X|no"}]]})

    def do_clear(self) -> int:
        """Видаляє ВСЕ в чаті (і невідомі боту повідомлення — за номерами), крім картки й непідтверджених
        шортсів. Telegram сам пропускає те, що старше 48 год."""
        keep = self.chat.keep_ids()
        top = max(self.chat.max_id(), max(self.chat.all_ids() | {0}))
        ids = [i for i in range(max(1, top - 5000), top + 1) if i not in keep]
        done = 0
        for i in range(0, len(ids), 100):
            part = ids[i:i + 100]
            try:
                self.api.call("deleteMessages", chat_id=self.cid, message_ids=part)
                done += len(part)
            except Exception:
                for mid in part:            # старіші версії API — по одному
                    try:
                        self.api.call("deleteMessage", chat_id=self.cid, message_id=mid)
                        done += 1
                    except Exception:
                        pass
        with self.chat.lock:
            self.chat.data["msgs"] = {k: v for k, v in self.chat.data["msgs"].items() if int(k) in keep}
            self.chat.save()
        self.send("🧹 Чат очищено: лишились картка статусу й непідтверджені шортси.", ttl=8)
        self.update_card(force=True)
        return done

    def short_markup(self, key: str, published: bool, video: bool = True) -> dict:
        """Кнопки під шортсом. video=True — повідомлення вже є відео (кнопка 480p не потрібна).
        Глядач («👁 перегляд») — без кнопок."""
        if self.role() not in CONTROL:
            return {"inline_keyboard": []}
        second = ([{"text": "↩ Повернути", "callback_data": f"u|{key}"}] if published else
                  [{"text": "✅ Підходить → до публікації", "callback_data": f"k|{key}"}])
        first = [{"text": "📱 Оригінал (повна якість)", "callback_data": f"f|{key}"}]
        if not video:
            first.insert(0, {"text": "▶ Переглянути 480p", "callback_data": f"v|{key}"})
        rows = [first, second + [{"text": "🗑 Видалити", "callback_data": f"d|{key}"}],
                [{"text": "🎬 Хайлайт цього бою", "callback_data": f"H|{key}"}]]
        if published:   # помітна плашка статусу над кнопками
            rows.insert(0, [{"text": "✅ ДО ПУБЛІКАЦІЇ ✅", "callback_data": f"n|{key}"}])
        return {"inline_keyboard": rows}

    def send_short(self, key: str, s: dict, cur: Path, pub: bool) -> bool:
        """Шортс — відеоповідомленням 480p (дивитись прямо в чаті), кнопки під відео.
        Непідтверджений — ПОСТІЙНИЙ у чаті (один раз; зникне після «✅ Підходить» / «🗑»);
        схвалений (список «✅ Схвалені») — тимчасовий. Не вийшло відео — текстом з «▶ Переглянути 480p»."""
        if not pub and self.chat.short(key):
            return False                                     # уже є в чаті
        ttl = None if not pub else TTL_BUTTONS
        try:
            self.api.call("sendChatAction", chat_id=self.cid, action="upload_video")
            prev = make_preview(self.settings, cur)
            if prev.stat().st_size > MAX_UPLOAD:
                raise RuntimeError("завеликий")
            res = self.api.send_file("sendVideo", "video", prev, chat_id=self.cid,
                               caption=self.short_text(s, cur, pub), supports_streaming="true",
                               width=480, height=854, duration=int(s["duration"]),
                               reply_markup=self.short_markup(key, pub, video=True))
            file_id = ((res or {}).get("video") or {}).get("file_id", "") if isinstance(res, dict) else ""
        except Exception as e:
            log.warning("Відео %s не надіслано: %s", s["name"], e)
            res = self.api.call("sendMessage", chat_id=self.cid, text=self.short_text(s, cur, pub),
                                reply_markup=self.short_markup(key, pub, video=False))
            file_id = ""
        mid = res.get("message_id") if isinstance(res, dict) else None
        if ttl is None:
            self.chat.set_short(key, mid, file_id)
            self._card_bottom = True                         # картка — під новими шортсами
        else:
            self.chat.add(mid, ttl, "approved")
        return True

    def drop_everywhere(self, key: str) -> None:
        """Шортс вирішено (хтось натиснув «Підходить» / «Видалити») — прибрати його з чатів УСІХ людей."""
        for c in self.access.recipients():
            with self.as_chat(c):
                mid = self.chat.drop_short(key)
                if mid:
                    self.delete_msg(mid)
                self.update_card(force=True)

    def broadcast_short(self, key: str, s: dict, mp4: Path) -> None:
        """Шортс знову на перевірці (повернули / відновили) — у чати всіх, у кого його немає."""
        for c in self.access.recipients():
            with self.as_chat(c):
                self.send_short(key, s, mp4, False)
                self.update_card(force=True)

    def locate(self, job_dir: Path, s: dict, marks: Marks) -> tuple[Path | None, bool]:
        """Де зараз MP4 шортсу: (шлях або None, чи в папці публікації)."""
        pub = marks.published(short_key(job_dir, s["i"]))
        if pub:
            return pub, True
        return (s["mp4"], False) if s["mp4"].exists() else (None, False)

    def short_text(self, s: dict, cur: Path, published: bool) -> str:
        mb = cur.stat().st_size / 1024 / 1024 if cur.exists() else 0
        head = ("✅ СХВАЛЕНО — у папці публікації\n" if published
                else "⬜ На перевірці: підходить чи видалити?\n")
        return (head + f"🎬 {s['name']}\n⏱ {s['duration']:.0f} с · {mb:.1f} МБ"
                + (f"\n📤 Файл: {cur.name}" if published else ""))

    def show_job(self, job_dir: Path, only_published: bool = False):
        marks = Marks(self.settings)
        items = []
        for s in job_shorts(job_dir):
            cur, pub = self.locate(job_dir, s, marks)
            if cur is None or (only_published and not pub):
                continue
            items.append((s, cur, pub))
        if not items:
            return 0
        n = 0
        for s, cur, pub in items:
            n += bool(self.send_short(short_key(job_dir, s["i"]), s, cur, pub))
        self.update_card(force=True)
        return n

    # -------------------------------------------------------------- команди
    def cmd_status(self):
        self.update_card(force=True, bottom=True)               # картка — і є статус
        st = read_status(self.settings) or {}
        s = st.get("status")
        age = time.time() - float(st.get("heartbeat") or 0)
        if s == "running" and age < 120:
            self.send(f"⏳ Обробка йде: {st.get('stage', '')} — {st.get('percent', 0)} %"
                      + (f"\nРезультат: {Path(st['job_dir']).name}" if st.get("job_dir") else ""))
        elif s == "running":
            self.send(f"⚠️ Обробка, схоже, зупинилась (немає вістей {fmt_time(age, 0)}). "
                      f"Останній етап: {st.get('stage', '')}")
        elif s == "done":
            self.send(f"✅ Остання обробка завершена: {st.get('message', '')}\n/shorts — показати шортси")
        elif s == "error":
            self.send(f"❌ Остання обробка з помилкою: {st.get('message', '')}")
        else:
            self.send("Обробок ще не було.")

    SHOW_BATCH = 5

    def cmd_shorts(self):
        """Непідтверджені шортси, яких ще немає в чаті (по SHOW_BATCH за раз, новіші першими)."""
        from autocut.bot.card import short_counts
        pending = short_counts(self.settings)["pending"]
        if not pending:
            self.send("Непідтверджених шортсів немає — усе перевірено 👍")
            return
        sent = 0
        for key, _j, s in pending:
            if sent >= self.SHOW_BATCH:
                break
            if self.chat.short(key):
                continue
            sent += bool(self.send_short(key, s, s["mp4"], False))
        if not sent:
            self.send(f"Усі непідтверджені ({len(pending)}) уже в чаті — вище.")
        self.update_card(force=True)

    def cmd_all(self):
        jobs = list_jobs(self.settings)[-12:]
        if not jobs:
            self.send("Результатів ще немає.")
            return
        rows = []
        for j in reversed(jobs):
            n = sum(1 for s in job_shorts(j) if not s["deleted"])
            rows.append([{"text": f"{j.name.removeprefix('job_')} — {n} шт.",
                          "callback_data": f"j|{j.name.removeprefix('job_')}"}])
        self.send("Результати (новіші — зверху):", {"inline_keyboard": rows})

    def cmd_kept(self):
        total = sum(self.show_job(j, only_published=True) for j in reversed(list_jobs(self.settings)))
        if total:
            self.send(f"📤 Схвалені — у папці публікації: {publish_dir(self.settings, self.cfg)}\n"
                      "(ці повідомлення зникнуть самі; «↩ Повернути» — назад на перевірку)", ttl=TTL_BUTTONS)
        else:
            self.send("У папці публікації поки нічого немає (кнопка «✅ Підходить» під шортсом).")

    # -------------------------------------------------------------- оновлення
    UPDATE_BTN = {"inline_keyboard": [[{"text": "🔄 Оновити зараз", "callback_data": "U|go"}]]}

    ROLLBACK_BTN = [{"text": "⏪ Відкотити версію", "callback_data": "B|list"}]

    def cmd_update(self):
        from autocut.cli import update
        here = f"Встановлено: версія {update.code_version()}"
        try:
            lv, rv = update.local_version(), update.remote_version()
        except Exception as e:
            self.send(f"{here}\nНе вдалося зв'язатися з GitHub: {e}", {"inline_keyboard": [self.ROLLBACK_BTN]})
            return
        if lv.get("sha") == rv["sha"]:
            self.send(f"✅ {here} — остання ({rv['sha'][:7]}: {rv['message']}).",
                      {"inline_keyboard": [self.ROLLBACK_BTN]})
            return
        news = update.changes_since(lv.get("sha"))
        text = f"{here}\n🔄 Є оновлення" + (f" — змін: {len(news)}:\n" + "\n".join(f"• {c}" for c in news) if news
                                            else f": {rv['message']}")
        self.send(text, {"inline_keyboard": self.UPDATE_BTN["inline_keyboard"] + [self.ROLLBACK_BTN]})

    def cmd_rollback(self):
        """Список збережених версій (backups\\) — кнопкою повернути будь-яку."""
        from autocut.cli import update
        items = update.list_backups()
        if not items:
            self.send("⏪ Збережених версій ще немає: копія робиться перед кожним оновленням, "
                      "тож з'явиться після наступного.")
            return
        self.rollback_names = {str(i): b["name"] for i, b in enumerate(items)}
        rows = [[{"text": f"⏪ {b['version']} · {b['created']}" + (f" · {b['message'][:30]}" if b.get("message") else ""),
                  "callback_data": f"B|{i}"}] for i, b in enumerate(items)]
        rows.append([{"text": "✖ Скасувати", "callback_data": "X|"}])
        self.send(f"Зараз версія {update.code_version()}. На яку повернутись? Налаштування, рамки, токен, "
                  "відео й кеш не змінюються; поточна версія теж збережеться — відкат можна скасувати.",
                  {"inline_keyboard": rows})

    def do_rollback(self, key: str):
        from autocut.cli import update
        name = (getattr(self, "rollback_names", {}) or {}).get(key)
        if not name:
            self.send("Список застарів — натисніть «⏪ Відкотити версію» ще раз.")
            return
        st = read_status(self.settings) or {}
        if st.get("status") == "running" and time.time() - float(st.get("heartbeat") or 0) < 120:
            self.send("⏳ Зараз іде обробка — відкочу, коли закінчиться (натисніть ще раз пізніше).")
            return
        try:
            res = update.restore_backup(name, log=lambda m: None)
        except Exception as e:
            self.send(f"❌ Відкат не вдався: {e}")
            return
        self.send(f"⏪ Повернуто версію {res['version']}. Бот перезапускається… Вікно програми закрийте й "
                  "відкрийте знову.")
        self.restart()

    def do_update(self):
        st = read_status(self.settings) or {}
        if st.get("status") == "running" and time.time() - float(st.get("heartbeat") or 0) < 120:
            self.send("⏳ Зараз іде обробка — оновлю, коли закінчиться (натисніть «Оновити» ще раз пізніше).")
            return
        from autocut.cli import update
        wait = self.send("🔄 Оновлюю з GitHub…")
        self.delete_later(wait.get("message_id") if isinstance(wait, dict) else None, 1.0)
        try:
            res = update.apply_update(log=lambda m: None)
        except Exception as e:
            self.send(f"❌ Оновлення не вдалося: {e}\nМожна запустити SETUP.bat на ПК.")
            return
        if res["up_to_date"]:
            self.send(f"✅ Уже остання версія ({res['sha'][:7]}).")
            return
        news = res.get("changes") or [res["message"]]
        self.send(f"✅ Оновлено до {res['sha'][:7]} (файлів: {res['files']}"
                  + (", бібліотеки доставлено" if res.get("requirements") else "") + "):\n"
                  + "\n".join(f"• {c}" for c in news)
                  + "\n\nБот перезапускається… Якщо відкрито вікно інструмента — закрийте й відкрийте його знову; "
                    "DaVinci — перезапустіть, щоб оновилась кнопка.")
        self.restart()

    def restart(self):
        """Новий процес бота з оновленим кодом; цей — завершується."""
        try:
            if self.lock is not None:
                self.lock.close()
        except Exception:
            pass
        exe = Path(sys.executable)
        pyw = exe.with_name("pythonw.exe") if exe.with_name("pythonw.exe").exists() else exe
        subprocess.Popen([str(pyw), str(ROOT / "tools" / "telegram_bot.py"), "--delay", "3"], cwd=str(ROOT),
                         creationflags=_NO_WINDOW)
        os._exit(0)

    UPDATE_CHECK_S = 3600       # автоматично — не частіше ніж раз на годину (і після перезапуску теж)

    def check_updates(self):
        """Не частіше ніж раз на годину: якщо на GitHub нова версія — повідомити (один раз на версію).
        Час останньої перевірки зберігається, тож перезапуски бота (після оновлень) не додають перевірок."""
        last = max(self.last_update_check, float(self.cfg.get("last_update_check") or 0))
        if time.time() - last < self.UPDATE_CHECK_S or self.owner is None:
            return
        self.last_update_check = time.time()
        self.cfg["last_update_check"] = self.last_update_check
        try:
            save_cfg(self.cfg)
        except Exception:
            pass
        from autocut.cli import update
        try:
            lv, rv = update.local_version(), update.remote_version()
        except Exception:
            return
        if lv.get("sha") and lv["sha"] != rv["sha"] and self.notified_sha != rv["sha"]:
            self.notified_sha = rv["sha"]
            self.cfg["notified_sha"] = rv["sha"]
            save_cfg(self.cfg)
            self.send(f"🆕 Вийшло оновлення: {rv['message']}", self.UPDATE_BTN)

    HELP = ("Кнопки внизу:\n"
            "📋 Нові шортси на перевірку — шортси останньої нарізки твого каналу: переглянути, «✅ Підходить» або «🗑»\n"
            "⏳ Що зараз робиться — що програма обробляє на ПК і скільки залишилось\n"
            "✅ Схвалені (до публікації) — шортси, які ти позначив «Підходить» (лежать у папці публікації)\n"
            "🗑 Видалені — відновити — останні видалені шортси, можна повернути\n"
            "📁 Усі нарізки — усі запуски автонарізки, відкрити будь-який\n"
            "🔄 Оновити програму — завантажити нову версію з GitHub\n"
            "🇬🇧 EU: реплеї → шортси — шортси для EU-каналу з чужих реплеїв: фільтр, підбірка, запис "
            "(файл .wotreplay можна просто надіслати сюди). Вони йдуть лише в чергу EU-каналу Poster\n"
            "🧹 Очистити чат — прибрати повідомлення бота\n"
            "/users — хто має доступ до бота (людина пише /start — вам приходить запит: 🛠 адмін або 👁 перегляд)\n\n"
            "Під шортсом: ▶ 480p — легкий перегляд, 📱 оригінал (якщо до 50 МБ), "
            "✅ Підходить — перенести MP4 (і субтитри) в папку публікації, ↩ Повернути — назад, "
            "🗑 Видалити — у output\\_видалені.")

    # -------------------------------------------------------------- кнопки
    TOASTS = {"X": "🧹 Готово", "R": "♻ Відновлено", "H": "🎬 Роблю хайлайт — пришлю, коли буде готовий", "P": "🎬 Роблю хайлайт з цього запису", "k": "✅ Підходить — перенесено в папку публікації", "u": "↩ Повернуто на перевірку",
              "D": "🗑 Видалено. Повернути — кнопка «🗑 Видалені — відновити» в меню", "n": "✅ Цей шортс уже в папці публікації", "c": "Скасовано",
              "v": "Готую відео…", "f": "Надсилаю оригінал…"}

    def _answer(self, cq: dict, text: str | None):
        try:
            self.api.call("answerCallbackQuery", callback_query_id=cq["id"], text=text, show_alert=False)
        except Exception:
            pass

    def on_callback(self, cq: dict):
        """Спливаюче повідомлення-підтвердження на кожну кнопку; довгі дії (відео, оновлення) — одразу."""
        act = cq.get("data", "").partition("|")[0]
        if act == "F" and self.replays is not None:     # фільтр і запис реплеїв
            try:
                toast = self.replays.on_callback(cq, cq.get("data", "").partition("|")[2])
            except Exception as e:
                self._answer(cq, f"Помилка: {e}"[:190])
                raise
            self._answer(cq, toast)
            return
        if act in ("v", "f", "U", "j", "H", "P", "B"):
            self._answer(cq, self.TOASTS.get(act))
            self._on_callback(cq)
            return
        try:
            self._on_callback(cq)
        except Exception as e:
            self._answer(cq, f"Помилка: {e}"[:190])
            raise
        self._answer(cq, self.TOASTS.get(act))

    def _on_callback(self, cq: dict):
        data = cq.get("data", "")
        act, _, key = data.partition("|")
        if act == "n":
            return
        if act == "C":
            self.cmd_shorts()
            return
        if act == "A":
            uid, _, role = key.partition("|")
            if uid == "invite":
                self.cmd_invite()
                return
            if uid.lstrip("-").isdigit() and role in ("admin", "viewer", "no"):
                self.set_access(int(uid), role, cq.get("message") or {})
            return
        if act == "X":
            msg = cq.get("message") or {}
            if key == "go":
                self.do_clear()
            elif msg:
                self.api.call("deleteMessage", chat_id=self.cid, message_id=msg["message_id"])
            return
        if act == "j":
            self.show_job(output_dir(self.settings) / f"job_{key}")
            return
        if act == "U":
            self.do_update()
            return
        if act == "B":
            msg = cq.get("message") or {}
            self.delete_msg(msg.get("message_id"))
            if key == "list":
                self.cmd_rollback()
            else:
                self.do_rollback(key)
            return
        if act == "P":
            msg = cq.get("message") or {}
            ch = self.rec_choices.pop(key, None)
            self.delete_msg(msg.get("message_id"))
            if not ch:
                self.send("Цей список уже застарів — натисніть «🎬 Хайлайт» ще раз.")
                return
            self.rec_choices.clear()
            self.start_highlight(ch[0], ch[1], ch[2])
            return
        found = find_short(self.settings, key)
        if not found:
            self.send("Цього шортсу вже немає.")
            return
        job_dir, s = found
        marks = Marks(self.settings)
        msg = cq.get("message") or {}
        if act == "R":
            entry = marks.deleted().get(key)
            if not entry or not Path(entry["moved"]).exists():
                self.delete_msg(msg.get("message_id"))
                return
            mp4 = restore_short(entry, s)
            marks.set_deleted(key, None)
            self.delete_msg(msg.get("message_id"))
            self.broadcast_short(key, s, mp4)
            return
        if act == "H":
            self.start_highlight(job_dir, s)
            return
        cur, pub = self.locate(job_dir, s, marks)
        if cur is None:
            self.chat.drop_short(key)
            self.delete_msg(msg.get("message_id") if (msg := cq.get("message") or {}) else None)
            self.send(f"«{s['name']}» уже видалено або перенесено вручну.")
            self.update_card(force=True)
            return
        msg = cq.get("message") or {}

        is_video = bool(msg.get("video") or msg.get("caption") is not None and msg.get("text") is None)

        def redraw(text: str | None = None, markup: dict | None = None):
            if not msg:
                return
            if text is not None and msg.get("text") is not None:
                self.api.call("editMessageText", chat_id=self.cid, message_id=msg["message_id"],
                              text=text, reply_markup=markup)
            elif text is not None and is_video:
                self.api.call("editMessageCaption", chat_id=self.cid, message_id=msg["message_id"],
                              caption=text, reply_markup=markup)
            else:
                self.api.call("editMessageReplyMarkup", chat_id=self.cid, message_id=msg["message_id"],
                              reply_markup=markup)

        vid = msg.get("text") is None          # кнопки під відео (не під текстом)
        if act in ("v", "f"):
            self.api.call("sendChatAction", chat_id=self.cid, action="upload_video")
            path = make_preview(self.settings, cur) if act == "v" else cur
            if path.stat().st_size > MAX_UPLOAD:
                self.send("Оригінал більший за 50 МБ — Telegram не дозволяє ботам таке надсилати. "
                          "Дивіться ▶ 480p.")
                return
            w, h = (480, 854) if act == "v" else (1080, 1920)
            res = self.api.send_file("sendVideo", "video", path, chat_id=self.cid, caption=self.short_text(s, cur, pub),
                               supports_streaming="true", width=w, height=h, duration=int(s["duration"]),
                               reply_markup={"inline_keyboard": []})
            self.track(res, TTL_BUTTONS)                     # лише подивитись — зникне саме
        elif act == "k" and not pub:
            new = move_with_srt(cur, publish_dir(self.settings, self.cfg))
            marks.set(key, new)
            self.chat.drop_short(key)
            self.delete_msg(msg.get("message_id"))           # перевірено — з чату зникає; лічильник — у картці
            self.drop_everywhere(key)                        # і з чатів інших людей
        elif act == "k":
            self.chat.drop_short(key)
            self.delete_msg(msg.get("message_id"))
        elif act == "u" and pub:
            back = move_with_srt(cur, s["mp4"].parent)
            if back != s["mp4"]:
                shutil.move(str(back), str(s["mp4"]))
                if back.with_suffix(".srt").exists():
                    shutil.move(str(back.with_suffix(".srt")), str(s["mp4"].with_suffix(".srt")))
            marks.set(key, None)
            self.delete_msg(msg.get("message_id"))
            self.broadcast_short(key, s, s["mp4"])           # знову на перевірку — у чати всіх
        elif act == "d":
            redraw(None, {"inline_keyboard": [[
                {"text": "🗑 Так, видалити", "callback_data": f"D|{key}"},
                {"text": "Скасувати", "callback_data": f"c|{key}"}]]})
        elif act == "D":
            entry = delete_short(self.settings, job_dir, s, cur)
            marks.set(key, None)
            marks.set_deleted(key, entry)
            self.chat.drop_short(key)
            self.delete_msg(msg.get("message_id"))      # пост зникає; відновити — «🗑 Видалені»
            self.drop_everywhere(key)
            log.info("Видалено %s -> %s", s["name"], entry["moved"])
        elif act == "c":
            redraw(None, self.short_markup(key, pub, vid))

    # -------------------------------------------------------------- цикл
    def handle(self, upd: dict):
        if "callback_query" in upd:
            cq = upd["callback_query"]
            uid = (cq.get("from") or {}).get("id")
            role = self.access.role(uid)
            act = cq.get("data", "").partition("|")[0]
            if role not in CONTROL:
                if role == "viewer":
                    self._answer(cq, "👁 У вас доступ лише на перегляд")
                return
            if act in OWNER_ACTS and role != "owner":
                self._answer(cq, "Це може лише власник бота")
                return
            chat = ((cq.get("message") or {}).get("chat") or {}).get("id") or uid
            with self.as_chat(chat):
                self.on_callback(cq)
            return
        m = upd.get("message") or {}
        uid = (m.get("from") or {}).get("id")
        text = (m.get("text") or "").strip()
        if self.owner is None and text.startswith("/start"):
            self.owner = uid
            self.cfg["owner_id"] = uid
            save_cfg(self.cfg)
        role = self.access.role(uid)
        if role not in ("owner", "admin", "viewer"):
            self.ask_access(m, role)          # чужий: /start — запит власнику; решта — нічого
            return
        with self.as_chat(uid):
            self._handle_message(m, role, text)

    def ask_access(self, m: dict, role: str | None) -> None:
        user = m.get("from") or {}
        uid = user.get("id")
        if uid is None or not (m.get("text") or "").strip().startswith("/start") or self.owner is None:
            return
        name = display_name(user)
        if role != "pending":
            self.access.set(uid, "pending", name)
            kb = {"inline_keyboard": [[{"text": "🛠 Адмін", "callback_data": f"A|{uid}|admin"},
                                       {"text": "👁 Перегляд", "callback_data": f"A|{uid}|viewer"},
                                       {"text": "❌ Відхилити", "callback_data": f"A|{uid}|no"}]]}
            self.send(f"🔑 Запит доступу до бота: {name}\n\n🛠 Адмін — перевіряє шортси (кнопки «✅ Підходить» / «🗑»)\n"
                      "👁 Перегляд — лише бачить шортси й статус", kb, ttl=7 * 24 * 3600)
        with self.as_chat(uid):
            self.api.call("sendMessage", chat_id=uid, text="🔑 Запит доступу надіслано власнику бота. "
                          "Щойно він підтвердить — тут з'являться шортси й статус.")

    def bot_username(self) -> str:
        if not getattr(self, "_username", ""):
            try:
                self._username = (self.api.call("getMe") or {}).get("username", "")
            except Exception:  # noqa: BLE001
                self._username = ""
        return self._username

    def cmd_users(self) -> None:
        """«👥 Доступ»: хто має доступ, запити, що чекають, і «➕ Додати адміністратора»."""
        users = {k: v for k, v in self.access.users.items() if v.get("role")}
        rows, lines = [], ["👥 Доступ до бота"]
        pending = {k: v for k, v in users.items() if v.get("role") == "pending"}
        active = {k: v for k, v in users.items() if v.get("role") != "pending"}
        if pending:
            lines.append("\n🔑 Чекають рішення:")
            for k, v in pending.items():
                lines.append(f"• {v.get('name') or k}")
                rows.append([{"text": f"🛠 Адмін: {v.get('name') or k}"[:40], "callback_data": f"A|{k}|admin"},
                             {"text": "👁 Перегляд", "callback_data": f"A|{k}|viewer"},
                             {"text": "❌", "callback_data": f"A|{k}|no"}])
        lines.append("\nМають доступ:" if active else "\nДоступ поки має лише власник (ви).")
        for k, v in active.items():
            lines.append(f"• {v.get('name') or k} — {ROLE_NAMES.get(v.get('role'), v.get('role'))}")
            rows.append([{"text": f"🛠 {v.get('name') or k}"[:30], "callback_data": f"A|{k}|admin"},
                         {"text": "👁", "callback_data": f"A|{k}|viewer"},
                         {"text": "🗑 Прибрати", "callback_data": f"A|{k}|no"}])
        lines.append("\n🛠 — адмін (перевіряє шортси), 👁 — лише перегляд, 🗑 — прибрати доступ.")
        rows.append([{"text": "➕ Додати адміністратора", "callback_data": "A|invite|admin"}])
        self.send("\n".join(lines), {"inline_keyboard": rows})

    def cmd_invite(self) -> None:
        name = self.bot_username()
        link = f"https://t.me/{name}?start=join" if name else ""
        self.send("➕ Як додати адміністратора:\n"
                  f"1. Перешліть людині посилання на бота: {link or '(знайдіть бота в Telegram за назвою)'}\n"
                  "2. Людина відкриває його й натискає «Start».\n"
                  "3. Вам приходить запит — натисніть «🛠 Адмін» (або «👁 Перегляд»).\n"
                  "Після цього людина одразу бачить шортси на перевірці й картку статусу.", ttl=30 * 60)

    def set_access(self, uid: int, role: str, msg: dict) -> None:
        name = self.access.name(uid)
        self.delete_msg(msg.get("message_id"))
        if role == "no":
            was = self.access.role(uid)
            self.access.remove(uid)
            self.send(f"🗑 {name}: доступу немає.")
            if was in ("admin", "viewer"):
                with self.as_chat(uid):
                    for key, v in list(self.chat.data["shorts"].items()):
                        self.chat.drop_short(key)
                        self.delete_msg(v.get("id"))
                    card = self.chat.card()
                    if card:
                        self.delete_msg(card["id"])
                    self.api.call("sendMessage", chat_id=uid, text="Доступ до бота закрито власником.",
                                  reply_markup={"remove_keyboard": True})
            return
        self.access.set(uid, role)
        self.send(f"✅ {name}: {ROLE_NAMES[role]}.")
        with self.as_chat(uid):                      # новій людині — меню, картка й шортси на перевірці
            self.send(f"✅ Вам відкрито доступ: {ROLE_NAMES[role]}." + (
                "\nПід шортсами — «✅ Підходить» / «🗑 Видалити»." if role == "admin" else
                "\nВи бачите шортси й статус; рішення приймає власник."), ttl=TTL_BUTTONS)
            self.cmd_shorts()
            self.update_card(force=True, bottom=True)

    def _handle_message(self, m: dict, role: str, text: str):
        self.chat.note_id(m.get("message_id"))
        self.track(m, TTL_INFO)
        mapped = self.MENU_CMDS.get(text, text)
        cmd0 = mapped.split()[0].split("@")[0].lower() if mapped else ""
        if role == "viewer" and cmd0 not in VIEWER_CMDS:
            self.delete_msg(m.get("message_id"))
            self.send("👁 У вас доступ лише на перегляд: шортси й статус. Рішення приймає власник.")
            return
        if role != "owner" and (cmd0 in OWNER_CMDS or m.get("document")):
            self.delete_msg(m.get("message_id"))
            self.send("Це може лише власник бота (оновлення програми, EU-реплеї, доступ).")
            return
        if m.get("document") and self.replays is not None:   # надісланий файл реплею
            self.replays.on_document(m)
            return
        if (self.replays is not None and self.replays.awaiting and text and not text.startswith("/")
                and text not in self.MENU_CMDS):             # число для порогу шкоди / засвіту
            if self.replays.on_text(text):
                self.delete_msg(m.get("message_id"))
                return
        if self.replays is not None:
            self.replays.awaiting = None                     # натиснули щось інше — питання знято
        text = self.MENU_CMDS.get(text, text)        # кнопки меню внизу чату
        cmd = text.split()[0].split("@")[0].lower() if text else ""
        if cmd == "/start":
            if role == "viewer":
                self.send("Привіт! Тут з'являтимуться нові шортси й статус (доступ на перегляд).")
                self.update_card(force=True, bottom=True)
            else:
                self.send("Привіт! Я бот автонарізки шортсів.\n\n" + self.HELP, self.menu())
        elif cmd == "/users":
            self.cmd_users()
        elif cmd == "/clear":
            self.cmd_clear()
        elif cmd == "/deleted":
            self.cmd_deleted()
        elif cmd == "/status":
            self.cmd_status()
        elif cmd == "/shorts":
            self.cmd_shorts()
        elif cmd == "/all":
            self.cmd_all()
        elif cmd == "/kept":
            self.cmd_kept()
        elif cmd == "/update":
            self.cmd_update()
        elif cmd == "/replays" and self.replays is not None:
            self.replays.show()
        else:
            self.send(self.HELP, self.menu())
        if cmd != "/start":
            self.delete_msg(m.get("message_id"))      # ваше натискання кнопки меню — прибираємо

    def watch_status(self):
        """Обробка на ПК: хід — у картці; готово — нові шортси одразу в чат на перевірку; помилка — коротко."""
        st = read_status(self.settings) or {}
        cur = (st.get("status"), st.get("job_id"))
        if self.last_status is None:
            self.last_status = cur
            return
        if cur == self.last_status or self.owner is None:
            return
        self.last_status = cur
        s = cur[0]
        if s == "done" and cur[1]:
            job_dir = output_dir(self.settings) / str(cur[1])
            shown = 0
            for c in self.access.recipients():               # власник, адміни, глядачі
                with self.as_chat(c):
                    shown = max(shown, self.show_job(job_dir))
            if not shown:
                self.send(f"✅ {st.get('message') or 'Обробка завершена'} — нових шортсів немає.")
        elif s == "error":
            self.send(f"❌ Обробка завершилась з помилкою: {st.get('message', '')}", ttl=TTL_ERROR)
        self.update_card(force=True)

    def migrate_content(self) -> None:
        """Один раз: старі папки з відео → Робочий стіл\\Контент (Мій канал / EU-канал), шляхи переписано."""
        from autocut.core.content import content_dir, ensure_migrated
        rdir = None
        try:
            if self.replays is not None:
                rdir = Path(self.replays.state()["replays_dir"])
        except Exception:  # noqa: BLE001
            pass
        before = self.cfg.get("publish_dir")
        moves = ensure_migrated(self.settings, self.cfg, rdir, log_fn=log.info)
        if self.cfg.get("publish_dir") != before:
            save_cfg(self.cfg)
        if moves and self.owner is not None:
            self.send("📁 Усе відео тепер в одному місці: " + str(content_dir(self.settings)) + "\n"
                      "  🎬 Мій канал: Нарізки · Хайлайти · До публікації\n"
                      "  🇬🇧 EU-канал: Нарізки · Записи реплеїв\n"
                      f"Перенесено папок: {len(moves)}; шляхи в історії оновлено. Poster підхопить нові папки сам.",
                      ttl=TTL_ERROR)

    # -------------------------------------------------------------- картка статусу й прибирання
    def update_card(self, force: bool = False, bottom: bool = False) -> None:
        """Одна картка статусу внизу чату. Змінився текст — редагуємо на місці; з'явились нові шортси
        (або картці ~46 год) — переносимо вниз: стару видаляємо, нову надсилаємо без звуку."""
        if self.owner is None:
            return
        from autocut.bot.card import card_text, short_counts
        text, when = self._card_last
        if not force and not bottom and not self._card_bottom and time.time() - when < 20:
            return
        try:
            text_new = card_text(self, short_counts(self.settings))
        except Exception as e:  # noqa: BLE001
            log.warning("Картка: %s", e)
            return
        card = self.chat.card()
        old = card is None or time.time() - float(card.get("t") or 0) >= REFRESH_AFTER
        if card is not None and not (old or bottom or self._card_bottom):
            if text_new.split("\n", 1)[1:] == text.split("\n", 1)[1:]:   # змінився лише час — не смикаємо
                self._card_last = (text, time.time())
                return
            try:
                self.api.call("editMessageText", chat_id=self.cid, message_id=card["id"], text=text_new)
                self._card_last = (text_new, time.time())
                return
            except Exception as e:  # noqa: BLE001
                if "not modified" in str(e):
                    self._card_last = (text_new, time.time())
                    return
                log.info("Картку не відредаговано (%s) — надсилаю нову", e)
        res = self.api.call("sendMessage", chat_id=self.cid, text=text_new, reply_markup=self.menu(),
                            disable_notification=True, disable_web_page_preview=True)
        if card is not None:
            self.delete_msg(card["id"])
        self.chat.set_card(res.get("message_id") if isinstance(res, dict) else None)
        self._card_last = (text_new, time.time())
        self._card_bottom = False

    def janitor(self) -> None:
        """Прибирає тимчасові повідомлення; непідтверджені шортси, яким ~46 год, переотправляє
        (за file_id — без завантаження), поки старі ще можна видалити (межа Telegram — 48 год)."""
        for mid in self.chat.due():
            self.delete_msg(mid)
        for key, v in self.chat.stale_shorts():
            found = find_short(self.settings, key)
            self.chat.drop_short(key)
            self.delete_msg(v.get("id"))
            if not found:
                continue
            job_dir, s = found
            cur, pub = self.locate(job_dir, s, Marks(self.settings))
            if cur is None or pub:
                continue
            if v.get("file_id"):
                try:
                    res = self.api.call("sendVideo", chat_id=self.cid, video=v["file_id"],
                                        caption=self.short_text(s, cur, False), supports_streaming=True,
                                        width=480, height=854, duration=int(s["duration"]),
                                        reply_markup=self.short_markup(key, False, video=True),
                                        disable_notification=True)
                    self.chat.set_short(key, res.get("message_id"), v["file_id"])
                    self._card_bottom = True
                    continue
                except Exception as e:  # noqa: BLE001
                    log.info("Переотправка за file_id не вдалась (%s) — надсилаю файл", e)
            self.send_short(key, s, cur, False)

    def reconcile(self) -> None:
        """Після запуску: кнопки під непідтвердженими шортсами — на місце (якщо зникли);
        зниклі з чату — надіслати знову; схвалені/видалені вже на ПК — прибрати з чату."""
        marks = Marks(self.settings)
        for key, v in list(self.chat.data["shorts"].items()):
            found = find_short(self.settings, key)
            cur, pub = self.locate(found[0], found[1], marks) if found else (None, False)
            if cur is None or pub:
                self.chat.drop_short(key)
                self.delete_msg(v.get("id"))
                continue
            try:
                self.api.call("editMessageReplyMarkup", chat_id=self.cid, message_id=v["id"],
                              reply_markup=self.short_markup(key, False, video=True))
            except Exception as e:  # noqa: BLE001
                if "not modified" in str(e):
                    continue
                log.info("Шортс %s у чаті не знайдено (%s) — надсилаю знову", key, e)
                self.chat.drop_short(key)
                self.send_short(key, found[1], cur, False)

    def run(self):
        try:
            self.api.call("setMyCommands", commands=[
                {"command": "shorts", "description": "Нові шортси на перевірку (мій канал)"},
                {"command": "status", "description": "Що зараз робиться на ПК"},
                {"command": "kept", "description": "Схвалені шортси (до публікації)"},
                {"command": "deleted", "description": "Видалені шортси — відновити"},
                {"command": "all", "description": "Усі нарізки"},
                {"command": "update", "description": "Оновити програму з GitHub"},
                {"command": "replays", "description": "🇬🇧 EU: шортси з реплеїв для EU-каналу"},
                {"command": "users", "description": "Хто має доступ до бота"},
                {"command": "clear", "description": "Очистити чат"}])
        except Exception as e:
            log.warning("setMyCommands: %s", e)
        log.info("Бот запущено, власник: %s", self.owner)
        self.migrate_content()
        for c in self.access.recipients():
            try:
                with self.as_chat(c):
                    self.reconcile()
                    self.janitor()
                    self.update_card(force=True, bottom=True)
            except Exception as e:  # noqa: BLE001
                log.warning("Чат %s після запуску: %s", c, e)
        stop = threading.Event()

        def watcher():
            while not stop.is_set():
                try:
                    self.watch_status()
                except Exception as e:
                    log.warning("Статус: %s", e)
                for c in self.access.recipients():
                    try:
                        with self.as_chat(c):
                            self.janitor()
                            self.update_card()
                    except Exception as e:
                        log.warning("Чат %s: %s", c, e)
                try:
                    self.check_updates()
                except Exception as e:
                    log.warning("Перевірка оновлень: %s", e)
                try:
                    if self.replays is not None:
                        self.replays.tick()
                except Exception as e:
                    log.warning("Реплеї: %s", e)
                stop.wait(10)

        threading.Thread(target=watcher, daemon=True).start()
        while True:
            try:
                ups = self.api.call("getUpdates", _timeout=60, timeout=40, offset=self.offset,
                                    allowed_updates=["message", "callback_query"])
                for u in ups:
                    self.offset = u["update_id"] + 1
                    try:
                        self.handle(u)
                    except Exception as e:
                        log.exception("Помилка обробки повідомлення")
                        if "query is too old" in str(e) or "message is not modified" in str(e):
                            continue                       # технічне, у чат не пишемо
                        try:
                            self.send(f"Помилка: {e}", ttl=TTL_ERROR)
                        except Exception:
                            pass
            except Exception as e:
                log.warning("Зв'язок з Telegram: %s — повтор через 15 с", e)
                time.sleep(15)


def single_instance() -> socket.socket | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", LOCK_PORT))
        s.listen(1)
        return s
    except OSError:
        return None


def main() -> int:
    settings = load_settings()
    setup_logging("telegram_bot", work_dir(settings) / "logs")
    cfg = load_cfg()
    if not cfg.get("token"):
        print("Бот не налаштований. Запустіть extra\\setup_telegram.bat.")
        return 1
    if "--delay" in sys.argv:                 # перезапуск після оновлення: дати старому процесу завершитись
        time.sleep(float(sys.argv[sys.argv.index("--delay") + 1]))
    lock = single_instance()
    if lock is None:
        log.info("Бот уже запущено — другий не потрібен.")
        return 0

    def control():
        # команда «stop» від extra\setup_telegram.bat (новий токен) — завершити бота
        while True:
            try:
                c, _ = lock.accept()
                if c.recv(16).startswith(b"stop"):
                    log.info("Бот зупинено (налаштування)")
                    os._exit(0)
                c.close()
            except Exception:
                time.sleep(1)

    threading.Thread(target=control, daemon=True).start()
    bot = Bot(settings, cfg)
    bot.lock = lock
    bot.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
