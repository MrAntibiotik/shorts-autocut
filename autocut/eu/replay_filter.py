"""Фільтр сайту реплеїв wot-record.com усередині програми.

Програма сама на сайт не ходить: вона лише складає адресу підбірки з вибраних фільтрів (так само, як кнопка
«Search» на сайті) і відкриває її у вашому браузері. Ви тиснете «Download» на боях, що сподобались, — а вкладка
«🎮 Реплеї» підхоплює файли з папки «Завантаження» (replay_download.import_folder) і обробляє їх сама.

Значення фільтрів (нації, рівні, танки, карти, версії…) — у config/wot_record_filters.json.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILTERS_FILE = ROOT / "config" / "wot_record_filters.json"
KEYS = ["nation", "tier", "type", "tank", "version", "game", "map", "mastery"]   # порядок — як на сайті


@lru_cache(maxsize=1)
def load_filters(path: str | None = None) -> dict:
    return json.loads(Path(path or FILTERS_FILE).read_text(encoding="utf-8-sig"))


def build_url(selection: dict, sort: str = "date", direction: str = "desc", site: str | None = None) -> str:
    """{'tier': ['10'], 'type': ['2', '3'], 'version': ['2.4']} → https://wot-record.com/replays/date/desc?tier=10&…"""
    site = (site or load_filters().get("site") or "https://wot-record.com").rstrip("/")
    parts = []
    for key in KEYS:
        vals = [str(v) for v in (selection.get(key) or []) if str(v).strip()]
        if vals:
            parts.append(f"{key}={urllib.parse.quote(','.join(vals), safe=',._')}")
    url = f"{site}/replays/{sort or 'date'}/{direction or 'desc'}"
    return url + ("?" + "&".join(parts) if parts else "")


def tanks_for(nations=(), tiers=(), types=(), search: str = "", filters: dict | None = None) -> list[dict]:
    """Танки, що підходять під вибрані нації/рівні/типи і пошуковий рядок (як список на сайті)."""
    f = filters or load_filters()
    nat = {int(n) for n in nations}
    tie = {int(t) for t in tiers}
    typ = {int(t) for t in types}
    s = search.strip().lower()
    out = []
    for t in f.get("tank", []):
        if nat and t.get("nation") not in nat:
            continue
        if tie and t.get("tier") not in tie:
            continue
        if typ and t.get("type") not in typ:
            continue
        if s and s not in t["label"].lower():
            continue
        out.append(t)
    return out


def version_key(v: str) -> tuple:
    return tuple(int(x) for x in re_digits(v)) or (0,)


def re_digits(v: str) -> list[str]:
    return re.findall(r"\d+", str(v or ""))


def latest_version(filters: dict | None = None, replays_dir: str | Path | None = None) -> str:
    """Найновіша відома версія «2.4»: зі списку фільтрів сайту або з реплеїв, що вже були в програмі
    (їхні .json у папці реплеїв) — так нові патчі підхоплюються самі, без оновлення списку."""
    cands = [o["value"] for o in (filters or load_filters()).get("version", [])]
    if replays_dir and Path(replays_dir).is_dir():
        for js in Path(replays_dir).glob("*.json"):
            try:
                v = json.loads(js.read_text(encoding="utf-8")).get("version") or ""
            except (OSError, ValueError):
                continue
            d = re_digits(v)
            if len(d) >= 2:
                cands.append(f"{d[0]}.{d[1]}")
    return max(cands, key=version_key) if cands else ""


def effective_version(explicit: str = "", client: tuple = (), filters: dict | None = None,
                      replays_dir: str | Path | None = None) -> tuple[str, tuple, str]:
    """Версія для фільтра й перевірки реплеїв: (значення «2.4», кортеж для перевірки, звідки).
    Пріоритет: вибрана вручну → ваша гра (version.xml) → найновіша відома."""
    if str(explicit or "").strip():
        v = str(explicit).strip()
        return v, tuple(int(x) for x in re_digits(v)), "вибрано"
    if client and len(client) >= 2:
        return f"{client[0]}.{client[1]}", tuple(client[:3]), "ваша гра"
    v = latest_version(filters, replays_dir)
    return v, tuple(int(x) for x in re_digits(v)), "найновіша"


def site_knows(version: str, filters: dict | None = None) -> bool:
    """Чи є така версія у фільтрі сайту (невідома версія в адресі → сайт падає з помилкою 500)."""
    known = {o["value"] for o in (filters or load_filters()).get("version", [])}
    d = re_digits(version)
    return not known or (len(d) >= 2 and f"{d[0]}.{d[1]}" in known)


def version_value(client: tuple, filters: dict | None = None) -> str:
    """Версія вашого клієнта у форматі фільтра сайту: (2, 4, 1, 1) → «2.4» (якщо така є у списку)."""
    if not client or len(client) < 2:
        return ""
    return f"{client[0]}.{client[1]}"   # нова версія, якої ще немає у списку, на сайті теж спрацює


__all__ = ["FILTERS_FILE", "KEYS", "build_url", "effective_version", "latest_version", "load_filters", "site_knows",
           "tanks_for",
           "version_key", "version_value"]
