"""Як записано відео: доріжки звуку (автовибір) і окремий файл вебкамери (пошук + синхронізація).

Рекомендована схема OBS (див. README):
  доріжка 1 — усе разом (гра + мікрофон), доріжка 2 — лише мікрофон, доріжка 3 — лише гра;
  вебкамера — окремим файлом поруч (плагін Source Record), назва «<як у запису>_webcam.mkv».

Автовибір: якщо в записі ≥ 3 доріжок звуку, а в settings.json режим "mixed" — інструмент сам
бере мікрофон з доріжки 2 і гру з доріжки 3 (recording.auto). Старі записи (1 доріжка) — як раніше.
"""

from __future__ import annotations

import copy
import logging
import re
from datetime import datetime
from pathlib import Path

import numpy as np

log = logging.getLogger("recording")

_STAMP = re.compile(r"(\d{4})-(\d{2})-(\d{2})[ _T](\d{2})-(\d{2})-(\d{2})")
_logged: set[str] = set()


def resolve_recording(settings: dict, info) -> dict:
    """Налаштування з урахуванням того, скільки доріжок у ЦЬОМУ записі (копія, оригінал не змінюється)."""
    rec = settings["recording"]
    auto = rec.get("auto") or {}
    if rec.get("mode") != "mixed" or not auto.get("enabled", True):
        return settings
    if info.audio_streams < int(auto.get("min_tracks", 3)):
        return settings
    s = copy.deepcopy(settings)
    sep = s["recording"]["separate"]
    sep["mic_audio_track"] = int(auto.get("mic_audio_track", 1))
    sep["game_audio_track"] = int(auto.get("game_audio_track", 2))
    s["recording"]["mode"] = "separate"
    key = str(info.path)
    if key not in _logged:
        _logged.add(key)
        log.info("Звук: у записі %d доріжок — беру мікрофон з доріжки %d, гру з доріжки %d "
                 "(окремі доріжки, без нейромережевого розділення)", info.audio_streams,
                 sep["mic_audio_track"] + 1, sep["game_audio_track"] + 1)
    return s


def _stamp(name: str) -> datetime | None:
    m = _STAMP.search(name)
    if not m:
        return None
    try:
        return datetime(*map(int, m.groups()))
    except ValueError:
        return None


def find_webcam_file(settings: dict, rec: Path) -> Path | None:
    """Файл вебкамери поруч із записом: «<назва запису><суфікс>.mkv» або з часом у назві,
    що відрізняється не більше ніж на 3 с (два записи стартували на межі секунди)."""
    sep = settings["recording"]["separate"]
    suffix = sep.get("webcam_file_suffix", "_webcam")
    dirs = [rec.parent] + [Path(d) for d in sep.get("webcam_search_dirs") or []]
    exts = (".mkv", ".mp4", ".mov", ".flv")
    for d in dirs:
        for ext in exts:
            p = d / f"{rec.stem}{suffix}{ext}"
            if p.exists():
                return p
    t0 = _stamp(rec.stem)
    if t0 is None:
        return None
    best, best_dt = None, 99.0
    for d in dirs:
        if not d.is_dir():
            continue
        for p in d.iterdir():
            if p.suffix.lower() in exts and suffix.lower() in p.stem.lower() and p != rec:
                t = _stamp(p.stem)
                if t is not None:
                    dt = abs((t - t0).total_seconds())
                    if dt <= 3 and dt < best_dt:
                        best, best_dt = p, dt
    return best


def webcam_offset(settings: dict, rec_info, cam_info) -> tuple[float, str]:
    """Зсув часу камери: час_у_файлі_камери = час_у_записі + offset.
    Якщо у файлі камери є звук — точно, за збігом зі звуком мікрофона в записі.
    Інакше — за часом у назвах файлів (точність ~1 с)."""
    from .audio import read_audio
    rec = settings["recording"]
    stamp_off = 0.0
    t_rec, t_cam = _stamp(rec_info.path.stem), _stamp(cam_info.path.stem)
    if t_rec and t_cam:
        stamp_off = (t_rec - t_cam).total_seconds()
    if cam_info.audio_streams == 0:
        return stamp_off, "за часом у назвах файлів (у файлі камери немає звуку — точність ~1 с)"
    try:
        sr, win, slack = 8000, 40.0, 5.0
        dur = min(rec_info.duration, cam_info.duration)
        t = min(120.0, max(slack, dur / 3))          # беремо шматок, де вже точно йде запис
        mic_track = rec["separate"]["mic_audio_track"] if rec["mode"] == "separate" else rec["mixed"]["audio_track"]
        a = read_audio(settings, rec_info.path, t, win, sr, 1, track=mic_track)
        b0 = max(0.0, t + stamp_off - slack)
        b = read_audio(settings, cam_info.path, b0, win + 2 * slack, sr, 1, track=0)
        if len(a) < sr * 5 or len(b) < len(a):
            raise ValueError("закороткий звук")
        from scipy.signal import correlate
        a = (a - a.mean()) / (a.std() + 1e-9)
        b = (b - b.mean()) / (b.std() + 1e-9)
        c = correlate(b, a, mode="valid", method="fft")
        k = int(np.argmax(c))
        peak = float(c[k])
        rest = np.abs(np.delete(c, slice(max(0, k - sr // 10), k + sr // 10)))
        conf = peak / (float(np.percentile(rest, 99.9)) + 1e-9) if rest.size else 0.0
        off = (b0 + k / sr) - t
        if conf < 1.5:
            return stamp_off, f"за часом у назвах (звук камери не збігся впевнено, {conf:.1f})"
        return round(off, 3), f"за звуком (збіг {conf:.1f})"
    except Exception as e:
        return stamp_off, f"за часом у назвах (звук не вдалося порівняти: {e})"
