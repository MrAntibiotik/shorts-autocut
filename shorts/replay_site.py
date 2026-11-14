"""Список боїв з wot-record.com і скачування одного реплею — ЛИШЕ на ваше натискання в боті.

Програма не обходить сайт сама й нічого не качає у фоні: одна сторінка списку — коли ви натиснули
«📋 Показати бої» / «➡ Далі», один файл — коли натиснули «⬇». Запити йдуть з чесним User-Agent,
стільки ж, скільки при перегляді сайту в браузері.
"""

from __future__ import annotations

import html as H
import re
import urllib.error
import urllib.request

UA = "ShortsAutocut/1.0 (user-requested replay list/download; one request per click)"
ITEM_RE = re.compile(r'<a class="tovabb" href="replay/(\d+)/([^"]*)"')
CELL_RE = re.compile(r'<img class="icon_(\w+)"[^>]*/>\s*<span[^>]*>([^<]*)</span>')


class SiteError(Exception):
    pass


def _num(text: str) -> int:
    d = re.sub(r"\D", "", text or "")
    return int(d) if d else 0


def _one(pattern: str, chunk: str) -> str:
    m = re.search(pattern, chunk, re.S)
    return H.unescape(m.group(1)).strip() if m else ""


def parse_list(html: str) -> list[dict]:
    """Бої зі сторінки списку wot-record.com."""
    starts = [(m.start(), m.group(1), m.group(2)) for m in ITEM_RE.finditer(html or "")]
    out, seen = [], set()
    for k, (pos, rid, slug) in enumerate(starts):
        if rid in seen:
            continue
        seen.add(rid)
        end = starts[k + 1][0] if k + 1 < len(starts) else len(html)
        chunk = html[pos:end]
        cells = {name: _num(val) for name, val in CELL_RE.findall(chunk)}
        info = _one(r'<div class="upload-info">(.*?)</div>', chunk)
        player = re.sub(r"<[^>]+>", "", info).split("|")[-1].strip() if "|" in info else ""
        mastery = _one(r"proficiency/class_icons_(\d)", chunk)
        tier = _one(r"tank_level_(\d+)", chunk)
        out.append({
            "id": rid, "slug": slug,
            "tank": _one(r'<span class="name[^"]*">([^<]*)</span>', chunk),
            "map": _one(r'<span class="map-name">([^<]*)</span>', chunk),
            "title": _one(r'<div class="title">([^<]*)</div>', chunk),
            "player": player,
            "tier": int(tier) if tier else 0,
            "mastery": int(mastery) if mastery else 0,
            "kills": cells.get("kills", 0), "damage": cells.get("damage", 0),
            "assist": cells.get("assist", 0), "blocked": cells.get("block", 0),
            "xp": cells.get("xp", 0), "views": cells.get("view", 0), "downloads": cells.get("download", 0),
        })
    return out


def _get(url: str, referer: str = "", timeout: float = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, **({"Referer": referer} if referer else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — адреса сайту з фільтра
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            raise SiteError(f"сайт відмовив ({e.code}) — спробуйте пізніше або скачайте в браузері") from e
        raise SiteError(f"сайт відповів помилкою {e.code}") from e
    except (urllib.error.URLError, OSError) as e:
        raise SiteError(f"немає зв'язку з сайтом: {e}") from e


def page_url(list_url: str, page: int) -> str:
    if page <= 1:
        return list_url
    return list_url + ("&" if "?" in list_url else "?") + f"page={page}"


def fetch_list(list_url: str, page: int = 1, getter=_get) -> list[dict]:
    """Одна сторінка списку (один запит)."""
    return parse_list(getter(page_url(list_url, page)).decode("utf-8", errors="replace"))


def download(site: str, rid: str, referer: str = "", getter=_get) -> bytes:
    """Один реплей (один запит)."""
    data = getter(f"{site.rstrip('/')}/download/{rid}", referer)
    if len(data) < 16 or data[:4] != b"\x12\x32\x34\x11":
        raise SiteError("сайт віддав не реплей (можливо, потрібен вхід або реплей видалено)")
    return data


MASTERY = {4: "Ace", 3: "I", 2: "II", 1: "III"}


def line(it: dict) -> str:
    """«40TP · Murovanka · 💥3 464 🔦707 · 5 фр. · Ace»."""
    def n(v):
        return f"{v:,}".replace(",", " ")
    parts = [f"{it['tank']} · {it['map']}", f"💥{n(it['damage'])}" + (f" 🔦{n(it['assist'])}" if it["assist"] else "")]
    if it["kills"]:
        parts.append(f"{it['kills']} фр.")
    if it["mastery"]:
        parts.append(MASTERY.get(it["mastery"], ""))
    return " · ".join(p for p in parts if p)


__all__ = ["SiteError", "download", "fetch_list", "line", "page_url", "parse_list"]
