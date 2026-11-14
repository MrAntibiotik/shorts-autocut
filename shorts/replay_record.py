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
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .replay_meta import ReplayInfo, parse_version

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
    load_s: float = 60.0          # завантаження гри й реплею до початку відліку
    tail_s: float = 20.0          # після кінця бою
    default_battle_s: float = 900  # якщо тривалість у реплеї невідома (бій не дограно)
    max_s: float = 1500


def game_exe(game_dir: Path) -> Path:
    for rel in GAME_EXES:
        p = Path(game_dir) / rel
        if p.exists():
            return p
    raise RecordError(f"У папці {game_dir} не знайдено WorldOfTanks.exe — вкажіть папку, куди встановлено гру "
                      "(напр. C:\\Games\\World_of_Tanks_EU)")


def client_version(game_dir: Path) -> tuple:
    """Версія встановленої гри з version.xml: «v.1.26.0.1 #1234» → (1, 26, 0, 1)."""
    try:
        text = (Path(game_dir) / "version.xml").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ()
    m = re.search(r"<version>\s*([^<]+)</version>", text)
    return parse_version(m.group(1))[1] if m else ()


def record_seconds(info: ReplayInfo | None, opts: RecordOptions) -> float:
    battle = info.duration_s if info and info.duration_s else opts.default_battle_s
    # +30 с — відлік перед боєм, який реплей теж показує
    return min(opts.max_s, opts.load_s + 30 + battle + opts.tail_s)


class Obs:
    """Тонка обгортка над obs-websocket v5 (пакет obsws-python)."""

    def __init__(self, host: str, port: int, password: str = ""):
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

    def start(self) -> None:
        if self.recording():
            raise RecordError("OBS уже записує — зупиніть запис вручну і спробуйте ще раз")
        self.cl.start_record()

    def stop(self) -> str:
        r = self.cl.stop_record()
        return str(getattr(r, "output_path", "") or "")


def kill_game() -> None:
    for name in PROCESS_NAMES:
        if WIN:
            subprocess.run(["taskkill", "/IM", name, "/T", "/F"], capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            subprocess.run(["pkill", "-f", name], capture_output=True)


def record_one(replay: Path, info: ReplayInfo | None, opts: RecordOptions, obs: Obs,
               progress=None, cancel=None) -> Path:
    """Відтворює один реплей і повертає шлях до запису OBS."""
    exe = game_exe(opts.game_dir)
    total = record_seconds(info, opts)
    kill_game()                    # гра з попереднього реплею могла лишитися
    time.sleep(2)
    log.info("Реплей %s: запускаю гру, запис ~%d хв", replay.name, round(total / 60))
    proc = subprocess.Popen([str(exe), str(Path(replay).resolve())], cwd=str(exe.parent))
    started = False
    try:
        obs.start()
        started = True
        t0 = time.time()
        while time.time() - t0 < total:
            if cancel and cancel():
                raise Cancelled()
            if progress:
                progress(f"Запис {replay.name}: {int(time.time() - t0)} / {int(total)} с", (time.time() - t0) / total)
            if proc.poll() is not None and time.time() - t0 > opts.load_s:
                log.info("Гру закрито раніше — зупиняю запис")
                break
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


__all__ = ["Cancelled", "Obs", "RecordError", "RecordOptions", "client_version", "game_exe", "kill_game",
           "record_one", "record_seconds"]
