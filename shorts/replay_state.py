"""Спільні налаштування реплеїв для вкладки «🎮 Реплеї» і Telegram-бота (work/replays_state.json)
і замок «зараз іде запис», щоб вкладка й бот не записували одночасно."""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from .config import work_dir

DEFAULT_CTA = "Day {day} · Ukrainian veteran on the road to 1000 subs — subscribe 🛡️♥️"
DEFAULTS = {
    "downloads_dir": str(Path.home() / "Downloads"),
    "min_damage": 4000, "min_kills": 0, "min_assist": 0,
    "game_dir": r"C:\Games\World_of_Tanks_EU",
    "publish_dir": "",
    "obs_host": "localhost", "obs_port": 4455, "obs_password": "",
    "load_s": 60, "cta": DEFAULT_CTA, "day_n": 1, "credit": True,
    "do_record": True, "do_cut": True,
    "sort": "date", "version": "", "idle_min": 3,
    "wr_sel": {"tier": ["8", "9", "10"], "mastery": ["4"]},
}
LOCK_STALE_S = 2 * 3600       # замок без оновлення довше — залишок від аварійного виходу


class Busy(Exception):
    """Запис/нарізка реплеїв уже йде (з вкладки або з бота)."""


def state_file(settings: dict) -> Path:
    return work_dir(settings) / "replays_state.json"


def load(settings: dict) -> dict:
    try:
        return json.loads(state_file(settings).read_text(encoding="utf-8"))
    except Exception:
        return {}


def save(settings: dict, data: dict) -> None:
    p = state_file(settings)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def effective(settings: dict) -> dict:
    """Налаштування з типовими значеннями; номер дня росте сам."""
    st = {**DEFAULTS, **load(settings)}
    st.setdefault("replays_dir", str(work_dir(settings) / "replays"))
    if not st.get("replays_dir"):
        st["replays_dir"] = str(work_dir(settings) / "replays")
    try:
        passed = (date.today() - date.fromisoformat(st.get("day_date", ""))).days
    except ValueError:
        passed = 0
    st["day"] = max(1, int(st.get("day_n", 1)) + max(0, passed))
    return st


def cta_text(st: dict) -> str:
    return str(st.get("cta") or "").replace("{day}", str(st["day"])).replace("{день}", str(st["day"]))


def lock_path(replays_dir: Path) -> Path:
    return Path(replays_dir) / ".running"


def busy(replays_dir: Path) -> bool:
    p = lock_path(replays_dir)
    try:
        return time.time() - p.stat().st_mtime < LOCK_STALE_S
    except OSError:
        return False


@contextmanager
def running(replays_dir: Path, who: str):
    """Замок на час запису/нарізки. who — «вкладка» / «бот». Busy, якщо вже зайнято."""
    p = lock_path(replays_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    if busy(replays_dir):
        try:
            other = p.read_text(encoding="utf-8").strip()
        except OSError:
            other = "?"
        raise Busy(f"запис реплеїв уже йде ({other})")
    p.write_text(f"{who}, PID {os.getpid()}", encoding="utf-8")
    try:
        yield lambda: os.utime(p)        # «я живий» — оновити час замка
    finally:
        try:
            p.unlink()
        except OSError:
            pass


__all__ = ["Busy", "DEFAULTS", "DEFAULT_CTA", "busy", "cta_text", "effective", "load", "lock_path", "running", "save",
           "state_file"]
