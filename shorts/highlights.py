"""Хайлайти сильних боїв: окреме горизонтальне відео 16:10 на 7–8 хв для звичайної публікації.

Коли: бій зі шкодою або засвітом ≥ min_value (за замовчуванням 7000).
Що потрапляє: ключові моменти бою (влучання, засвіт, знищення, отримана шкода, ваші емоції) з
запасом до/після; коротші за merge_gap_s проміжки між ними не вирізаються (щоб не «стрибало»).
Запас підбирається автоматично, щоб тривалість була в межах target_min_s…target_max_s.
Бій, коротший за target_max_s, іде цілим. Початок і кінець бою — завжди.

Кадр (highlights → frame):
  "stream" (за замовчуванням) — увесь кадр стріму як є, у рідній роздільності (3440×1440 лишається 3440×1440):
           вебка, лог шкоди й усе інше — там, де їх бачили глядачі, нічого не вирізається й не накладається;
  "crop16x10" — центр гри 16:10 (2304×1440) + вебка в кутку й блок лічильників під нею накладками.
Opener підлаштовується під розмір кадру.
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
    "frame": "stream",         # stream — увесь кадр стріму як є; crop16x10 — центр 16:10 з накладками
    "output": "auto",          # «auto» — рідна роздільність запису: висота запису × 16:10 (3440×1440 → 2304×1440)
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
    fps = int(profile["layout"]["output"].get("fps", 60))
    if cfg.get("frame", "stream") != "crop16x10":            # увесь кадр стріму, без обрізання й накладок
        W, H = int(rec_w) // 2 * 2, int(rec_h) // 2 * 2
        return Layout(W, H, fps, "highlight",
                      [Layer("game", 1, "recording", Rect(0, 0, W, H), Rect(0, 0, W, H), rec_w, rec_h)],
                      int(H * 0.85), [], 0)
    out = cfg.get("output")
    if isinstance(out, dict) and out.get("width") and out.get("height"):
        W, H = int(out["width"]), int(out["height"])
    else:                                   # рідна роздільність гри — без масштабування (лише обрізання)
        H = int(rec_h) // 2 * 2
        W = int(round(H * 16 / 10)) // 2 * 2
        if W > rec_w:                       # запис вужчий за 16:10 (напр. 4:3) — по ширині
            W = int(rec_w) // 2 * 2
            H = int(round(W * 10 / 16)) // 2 * 2
    k = H / 1200.0                          # розміри вебки/лічильників/відступів задані для 1200 px
    cfg = dict(cfg, margin=float(cfg["margin"]) * k, webcam_width=float(cfg["webcam_width"]) * k,
               counters_height=float(cfg["counters_height"]) * k)
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


# ====================================================================== напис-заклик

def cta_overlays(settings: dict, layout: Layout, dur: float, tmp) -> list:
    """«Підпишись — підтримай ветерана 🛡️♥️»: на at_s секунді і в останні end_s секунд хайлайту."""
    from pathlib import Path

    from .render import Overlay
    from .textimg import render_text
    c = settings.get("highlight_cta") or {}
    text = (c.get("text") or "").strip()
    if not text:
        return []
    W, H = layout.width, layout.height
    img = render_text(text, settings["subtitles"], font_size=int(H * float(c.get("font_size_pct", 6.0)) / 100),
                      max_width=int(W * 0.8))
    tmp = Path(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    png = tmp / "highlight_cta.png"
    img.save(png)
    x = (W - img.width) // 2
    y = max(0, min(H - img.height, int(H * float(c.get("y_pct", 86.0)) / 100) - img.height // 2))
    show, at, end = float(c.get("show_s", 6.0)), float(c.get("at_s", 30.0)), float(c.get("end_s", 6.0))
    out = []
    if at > 0 and at + show < dur - max(end, 0) - 5:
        out.append(Overlay(png, at, at + show, x, y))
    if end > 0:
        out.append(Overlay(png, max(0.0, dur - end), dur, x, y))
    return out


# ====================================================================== json для Poster

def poster_texts(settings: dict, b: dict) -> dict:
    """Назва, опис і хештеги хайлайту для Poster (YouTube, звичайне відео)."""
    dmg, assist = int(b.get("damage_total") or 0), int(b.get("assist_total") or 0)
    parts = []
    if dmg:
        parts.append(f"{dmg} шкоди")
    if assist:
        parts.append(f"{assist} засвіту")
    stats = " і ".join(parts)
    title = f"{stats} — хайлайт бою | World of Tanks 🔥" if stats else "Хайлайт бою | World of Tanks 🔥"
    cta = ((settings.get("highlight_cta") or {}).get("text") or "").strip()
    tags = ["#wot", "#worldoftanks", "#танки", "#wotua"]
    desc = "\n".join(x for x in [
        f"Хайлайт бою: {stats}." if stats else "Хайлайт бою.",
        "Найяскравіші моменти — на початку відео.",
        "",
        cta,
    ] if x is not None).strip()
    return {"title": title[:100], "description": desc, "hashtags": tags}


def write_poster_json(settings: dict, b: dict, mp4) -> None:
    """Поруч із хайлайтом — json для Poster (окрема черга хайлайтів, лише YouTube).
    Уже опублікований (Poster переносить файли в posted/) не чіпаємо."""
    import json
    from pathlib import Path
    js = Path(mp4).with_suffix(".json")
    if js.exists():
        try:
            if json.loads(js.read_text(encoding="utf-8-sig")).get("status") == "posted":
                return
        except ValueError:
            pass
    t = poster_texts(settings, b)
    data = {"status": "ready_to_post", "kind": "highlight", "game": "WoT",
            "battle": {"index": b.get("index"), "damage": int(b.get("damage_total") or 0),
                       "assist": int(b.get("assist_total") or 0)},
            "youtube": {"title": t["title"], "description": t["description"],
                        "tags": [h.lstrip("#") for h in t["hashtags"]], "hashtags": t["hashtags"]}}
    tmp = js.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(js)


# ====================================================================== повна збірка (обробка, бот, extra\make_highlight.bat)

def make_highlight(settings: dict, info, profile: dict, b: dict, webcam: dict | None, mp4,
                   work, rec_start: float, rec_end: float) -> dict:
    """Рендер хайлайту бою + opener на початок. Повертає опис для job.json. Помилка рендеру — виняток;
    помилка opener'а — хайлайт без нього."""
    from pathlib import Path

    from .render import render_short
    from .util import fmt_time
    hcfg = cfg_of(profile)
    layout = compute_highlight_layout(profile, info.width, info.height, webcam)
    segs = select_highlight(b, hcfg, rec_start, rec_end)
    dur = sum(e - s for s, e in segs)
    mp4 = Path(mp4)
    log.info("  Хайлайт бою %d (%d): %s, шматків %d", b["index"], battle_value(b), fmt_time(dur, 0), len(segs))
    cam_video = webcam if any(l.source == "webcam_file" for l in layout.layers) else None
    overlays = cta_overlays(settings, layout, dur, Path(work) / f"{mp4.stem}_cta")
    render_short(settings, info, layout, segs, overlays, mp4, cam_video)
    opened, note = False, ""
    from .opener import cfg_of as ocfg_of, join, make_opener, pick_moments
    ocfg = ocfg_of(hcfg)
    if not ocfg.get("enabled"):
        note = "вимкнено в профілі (highlights → opener → enabled)"
    else:
        starts = pick_moments(_points(b), segs, ocfg)
        if not starts:
            note = f"яскравих моментів менше 3 (у бою знайдено {len(_points(b))})"
            log.info("    opener: %s — хайлайт без opener'а", note)
        else:
            try:
                op = make_opener(settings, ocfg, mp4, starts, Path(work) / f"{mp4.stem}_opener.mp4",
                                 (layout.width, layout.height))
                joined = mp4.with_name(mp4.stem + "_with_opener.mp4")
                join(settings, op, mp4, joined, layout.width, layout.height, layout.fps)
                joined.replace(mp4)
                opened = True
                log.info("    opener: %d моментів додано на початок", len(starts))
            except Exception as e:
                note = str(e).strip()[-400:] or type(e).__name__
                log.warning("[УВАГА] Opener не зроблено (%s) — хайлайт без нього", note)
    try:
        write_poster_json(settings, b, mp4)
    except OSError as e:
        log.warning("json для Poster не записано: %s", e)
    return {"battle": b["index"], "mp4": str(mp4), "duration": round(dur, 1), "opener": opened, "opener_note": note,
            "segments": [{"s": s, "e": e} for s, e in segs]}
