"""Зв'язок з Poster: де лежать папки готових шортсів кожного каналу.

Poster (основний бот) при кожному запуску записує %LOCALAPPDATA%\\MrAntibiotik\\poster.json:
  {"ready": <папка основного каналу>, "channels": {"en": {"name": …, "ready": <папка EN-каналу>}}}
Звідси «Реплеї» беруть папку EU/EN-каналу без налаштувань, і звідси ж знаємо папку основного каналу —
щоб шортс з реплею НІКОЛИ не потрапив у чергу основного каналу (це різний контент).
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def discovery_file() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".config")
    return Path(base) / "MrAntibiotik" / "poster.json"


def poster_paths() -> dict | None:
    try:
        return json.loads(discovery_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def channel_ready(key: str = "en") -> Path | None:
    """Папка готових шортсів додаткового каналу Poster (EN / EU), або None, якщо Poster її не повідомив."""
    data = poster_paths() or {}
    ch = (data.get("channels") or {}).get(key) or {}
    return Path(ch["ready"]) if ch.get("ready") else None


def main_ready() -> Path | None:
    data = poster_paths() or {}
    return Path(data["ready"]) if data.get("ready") else None


def _res(p: Path) -> Path:
    try:
        return Path(p).resolve()
    except OSError:
        return Path(p)


def is_main_channel_folder(folder: Path) -> bool:
    """True, якщо folder — папка основного каналу Poster або всередині неї (туди реплеї класти не можна).
    Папка EN-каналу може лежати всередині папки програми Poster, але не всередині ready основного каналу."""
    main = main_ready()
    if main is None:
        return False
    f, m = _res(folder), _res(main)
    en = channel_ready("en")
    if en is not None and (f == _res(en) or _res(en) in f.parents):
        return False
    return f == m or m in f.parents
