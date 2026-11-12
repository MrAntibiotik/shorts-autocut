"""Хайлайти сильних боїв: окреме горизонтальне відео 16:10 на 7–8 хв для звичайної публікації.

Коли: бій зі шкодою або засвітом ≥ min_value (за замовчуванням 7000).
Що потрапляє: ключові моменти бою (влучання, засвіт, знищення, отримана шкода, ваші емоції) з
запасом до/після; коротші за merge_gap_s проміжки між ними не вирізаються (щоб не «стрибало»).
Запас підбирається автоматично, щоб тривалість була в межах target_min_s…target_max_s.
Бій, коротший за target_max_s, іде цілим. Початок і кінець бою — завжди.

Кадр 1920×1200 (16:10): центр гри на весь кадр, вебкамера — у кутку, блок лічильників — під нею.
"""

from __future__ import annotations

import logging

from . import events as E
from .config import zone_px
from .layout import Layer, Layout, Rect, _clamp_rect, _cover

log = logging.getLogger("highlights")

DEFAULTS = {
    "enabled": False,
    "min_value": 7000,
    "target_min_s": 420,
    "target_max_s": 480,
    "base_pre_s": 8.0,
    "base_post_s": 6.0,
    "merge_gap_s": 20.0,
    "include_start_s": 20.0,
    "include_end_s": 10.0,
    "output": {"width": 1920, "height": 1200},
    "webcam_width": 480,
    "webcam_corner": "top_left",
    "margin": 24,
    "counters_height": 150,
}


def cfg_of(profile: dict) -> dict:
    c = dict(DEFAULTS)
    c.update(profile.get("highlights") or {})
    return c


def battle_value(b: dict) -> int:
    return max(int(b.get("damage_total") or 0), int(b.get("assist_total") or 0))


def wanted(b: dict, cfg: dict) -> bool:
    return bool(cfg.get("enabled")) and battle_value(b) >= int(cfg["min_value"])


# ====================================================================== вибір шматків

def _points(b: dict) -> list[tuple[float, float]]:
    """(час, вага) ключових моментів бою."""
    pts = []
    for e in b["events"]:
        if e.type in (E.HIT, E.ASSIST, E.KILL, E.DEATH, E.DAMAGE_TAKEN) and e.extra.get("moment", True):
            pts.append((e.t, max(0.3, float(e.extra.get("score", 1.0)))))
        elif e.type == E.VOICE and float(e.extra.get("intensity", 0)) >= 1.0:
            pts.append((e.t, 0.5 * float(e.extra.get("intensity", 1.0))))
    return sorted(pts)


def _build(pts, a: float, z: float, pre: float, post: float, cfg: dict, drop: set[int]) -> list[list[float]]:
    segs = [[a, min(z, a + cfg["include_start_s"])], [max(a, z - cfg["include_end_s"]), z]]
    segs += [[max(a, t - pre), min(z, t + post)] for i, (t, _w) in enumerate(pts) if i not in drop]
    segs.sort()
    out: list[list[float]] = []
    for s in segs:
        if out and s[0] - out[-1][1] <= cfg["merge_gap_s"]:
            out[-1][1] = max(out[-1][1], s[1])
        else:
            out.append(list(s))
    return out


def _total(segs) -> float:
    return sum(b - a for a, b in segs)


def select_highlight(b: dict, cfg: dict, rec_start: float, rec_end: float) -> list[tuple[float, float]]:
    a = max(rec_start, float(b["start"]))
    z = min(rec_end, float(b.get("stop") or b["end"]) + 3.0)
    tmax, tmin = float(cfg["target_max_s"]), float(cfg["target_min_s"])
    if z - a <= tmax:
        return [(a, z)]                                     # бій короткий — цілим
    pts = _points(b)
    pre0, post0 = float(cfg["base_pre_s"]), float(cfg["base_post_s"])
    drop: set[int] = set()

    def total(k: float) -> float:
        return _total(_build(pts, a, z, pre0 * k, post0 * k, cfg, drop))

    # базовий запас уже задовгий — прибираємо найслабші моменти
    order = sorted(range(len(pts)), key=lambda i: pts[i][1])
    while total(1.0) > tmax and len(drop) < len(pts):
        drop.add(order[len(drop)])
    # збільшуємо запас (k), поки влазить у tmax — найбільший k бінарним пошуком
    lo, hi = 1.0, 1.0
    while total(hi) < tmax and hi < 64:
        hi *= 2
    for _ in range(25):
        mid = (lo + hi) / 2
        if total(mid) <= tmax:
            lo = mid
        else:
            hi = mid
    segs = _build(pts, a, z, pre0 * lo, post0 * lo, cfg, drop)
    if _total(segs) < tmin:
        log.info("    хайлайт коротший за %.0f хв — у бою мало подій", tmin / 60)
    return [(s, e) for s, e in segs]


# ====================================================================== кадр 16:10

def compute_highlight_layout(profile: dict, rec_w: int, rec_h: int, webcam_file: dict | None = None) -> Layout:
    cfg = cfg_of(profile)
    W, H = int(cfg["output"]["width"]), int(cfg["output"]["height"])
    fps = int(profile["layout"]["output"].get("fps", 60))
    zones = profile.get("zones") or {}
    m = float(cfg["margin"])
    layers: list[Layer] = []
    warnings: list[str] = []

    # гра: центр кадру під 16:10; вебкамеру з кадру (якщо зліва/справа) — по змозі не захоплюємо
    game_src = _cover(Rect(0, 0, rec_w, rec_h), W / H)
    cam_zone = Rect(*zone_px(zones["webcam"], rec_w, rec_h)) if zones.get("webcam") and webcam_file is None else None
    if cam_zone is not None and game_src.overlaps(cam_zone):
        if cam_zone.x + cam_zone.w / 2 < rec_w / 2:
            game_src = _clamp_rect(Rect(cam_zone.x1, game_src.y, game_src.w, game_src.h), rec_w, rec_h)
        else:
            game_src = _clamp_rect(Rect(cam_zone.x - game_src.w, game_src.y, game_src.w, game_src.h), rec_w, rec_h)
    layers.append(Layer("game", 1, "recording", game_src, Rect(0, 0, W, H), rec_w, rec_h))

    # вебкамера в кутку
    cam_dst = None
    if webcam_file is not None:
        cam_src_full, src_kind = Rect(0, 0, webcam_file["width"], webcam_file["height"]), "webcam_file"
        csw, csh = int(webcam_file["width"]), int(webcam_file["height"])
    elif cam_zone is not None:
        cam_src_full, src_kind, csw, csh = cam_zone, "recording", rec_w, rec_h
    else:
        cam_src_full = None
    if cam_src_full is not None:
        cw = float(cfg["webcam_width"])
        ch = cw * cam_src_full.h / cam_src_full.w
        x = m if "left" in cfg["webcam_corner"] else W - m - cw
        y = m if "top" in cfg["webcam_corner"] else H - m - ch
        cam_dst = Rect(x, y, cw, ch)
        layers.append(Layer("webcam", 2, src_kind, cam_src_full, cam_dst, csw, csh))

    # блок лічильників — під вебкамерою (або в лівому верхньому куті)
    if zones.get("counters_block"):
        cz = Rect(*zone_px(zones["counters_block"], rec_w, rec_h))
        if not (game_src.x <= cz.x and cz.x1 <= game_src.x1):        # у кадр гри не потрапляє — накладаємо
            k = float(cfg["counters_height"]) / cz.h
            x = cam_dst.x if cam_dst is not None else m
            y = (cam_dst.y1 + 12) if cam_dst is not None and cam_dst.y < H / 2 else m
            layers.append(Layer("counters", 3, "recording", cz, Rect(x, y, cz.w * k, cz.h * k), rec_w, rec_h))
    return Layout(W, H, fps, "highlight", layers, int(H * 0.85), warnings, 0)
