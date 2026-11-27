"""Обрізка вебки «як на стрімі»: з налаштувань сцен OBS.

Окремий файл вебки пише фільтр Source Record — це ВЕСЬ кадр камери (з кімнатою, полицями…). На стрімі ж
камера в сцені обрізана (Alt+перетягування або «Трансформація → Обрізка») — і, можливо, фільтром «Обрізка/Доповнення».
Тут ми читаємо збережений набір сцен OBS (%APPDATA%\\obs-studio\\basic\\scenes\\*.json), знаходимо джерело з
фільтром Source Record (вебка), його обрізку в сцені — і повертаємо частки кадру [x, y, w, h] для «webcam_file».

Ручна обрізка в майстрі («✂ Обрізати вебку…») має перевагу над цією.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

RECORD_FILTERS = ("source_record_filter", "source_record")
CAM_SOURCES = ("dshow_input", "av_capture_input", "v4l2_input", "macos_avcapture")
CROP_FILTERS = ("crop_filter",)


def scenes_dir() -> Path | None:
    base = os.environ.get("APPDATA")
    if not base:
        return None
    d = Path(base) / "obs-studio" / "basic" / "scenes"
    return d if d.is_dir() else None


def _load_collections(d: Path | None) -> list[tuple[Path, dict]]:
    out = []
    if not d or not Path(d).is_dir():
        return out
    for f in sorted(Path(d).glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):   # свіжіші — першими
        if f.name.endswith(".bak"):
            continue
        try:
            out.append((f, json.loads(f.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    return out


def _webcam_source(data: dict) -> dict | None:
    """Джерело, яке пише окремий файл: з фільтром Source Record (камера — першою)."""
    srcs = data.get("sources") or []
    rec = [s for s in srcs if any((f.get("id") or "") in RECORD_FILTERS for f in s.get("filters") or [])]
    rec.sort(key=lambda s: (s.get("id") not in CAM_SOURCES,))
    return rec[0] if rec else None


def _scene_items(data: dict, scene_name: str) -> list[dict]:
    for s in data.get("sources") or []:
        if s.get("id") in ("scene", "group") and s.get("name") == scene_name:
            return list((s.get("settings") or {}).get("items") or [])
    return []


def _find_item(data: dict, cam_name: str) -> dict | None:
    """Елемент сцени з вебкою: спершу в поточній сцені (і вкладених), потім — будь-де, де є обрізка."""
    scenes = [s for s in data.get("sources") or [] if s.get("id") in ("scene", "group")]
    cur = data.get("current_scene") or data.get("current_program_scene")
    order = [cur] + [s.get("name") for s in scenes if s.get("name") != cur] if cur else [s.get("name") for s in scenes]
    found = []
    for name in order:
        seen, stack = set(), [name]
        while stack:
            sc = stack.pop()
            if sc in seen:
                continue
            seen.add(sc)
            for it in _scene_items(data, sc):
                if it.get("name") == cam_name:
                    found.append(it)
                elif _scene_items(data, it.get("name")):
                    stack.append(it.get("name"))                 # вкладена сцена / група
        if found:
            break
    vis = [it for it in found if it.get("visible", True)] or found
    with_crop = [it for it in vis if any(int(it.get(k) or 0) for k in ("crop_left", "crop_top", "crop_right",
                                                                         "crop_bottom"))]
    return (with_crop or vis or [None])[0]


def _filter_crop(src: dict) -> tuple[int, int, int, int]:
    """Фільтр «Обрізка/Доповнення» ПІСЛЯ Source Record (до нього — файл уже обрізаний, тож не рахуємо)."""
    l = t = r = b = 0
    after_record = not any((f.get("id") or "") in RECORD_FILTERS for f in src.get("filters") or [])
    for f in src.get("filters") or []:
        fid = f.get("id") or ""
        if fid in RECORD_FILTERS:
            after_record = True
            continue
        if fid in CROP_FILTERS and after_record and f.get("enabled", True):
            s = f.get("settings") or {}
            if s.get("relative", True):
                l, t = l + int(s.get("left") or 0), t + int(s.get("top") or 0)
                r, b = r + int(s.get("right") or 0), b + int(s.get("bottom") or 0)
    return l, t, r, b


def webcam_crop(width: int, height: int, d: Path | None = None) -> tuple[list[float], str] | None:
    """[x, y, w, h] частками кадру вебки, як вона обрізана в сцені OBS, і звідки взято; None — обрізки немає."""
    if not width or not height:
        return None
    for f, data in _load_collections(d if d is not None else scenes_dir()):
        src = _webcam_source(data)
        if not src:
            continue
        item = _find_item(data, src.get("name")) or {}
        fl, ft, fr, fb = _filter_crop(src)
        l = int(item.get("crop_left") or 0) + fl
        t = int(item.get("crop_top") or 0) + ft
        r = int(item.get("crop_right") or 0) + fr
        b = int(item.get("crop_bottom") or 0) + fb
        if not (l or t or r or b):
            continue
        w, h = width - l - r, height - t - b
        if w < width * 0.1 or h < height * 0.1:
            continue                                           # дивна обрізка (інша роздільність) — не віримо
        crop = [round(l / width, 4), round(t / height, 4), round(w / width, 4), round(h / height, 4)]
        return crop, f"OBS «{f.stem}», джерело «{src.get('name')}»: обрізка ←{l} ↑{t} →{r} ↓{b} пкс"
    return None


def apply_to_webcam(webcam: dict | None, profile: dict) -> None:
    """Якщо вручну вебку не обрізали — взяти обрізку з OBS (webcam["crop"])."""
    if not webcam or (profile.get("zones") or {}).get("webcam_file"):
        return
    try:
        got = webcam_crop(int(webcam.get("width") or 0), int(webcam.get("height") or 0))
    except Exception as e:  # noqa: BLE001
        log.warning("Обрізка вебки з OBS не прочиталась: %s", e)
        return
    if got:
        webcam["crop"], how = got
        log.info("Вебка обрізана як на стрімі — %s", how)
