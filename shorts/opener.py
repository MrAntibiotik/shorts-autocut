"""Opener для хайлайту: 3–6 найяскравіших моментів бою в шаблоні MR_ANTIBIOTIK (templates/opener_16x10),
потім — сам хайлайт. Шаблон збирає templates/opener_16x10/fill_template.py (музика, переходи, HUD, заставка).

Моменти беруться з УЖЕ готового хайлайту (там вебкамера й лічильники на місцях): найсильніший — ПІК
(останній), другий за силою — HOOK (перший), решта — між ними за часом.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from .config import ROOT
from .media import find_tool

log = logging.getLogger("opener")
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

DEFAULTS = {"enabled": True, "template_dir": "templates/opener_16x10", "moments": 5, "pre_s": 0.5,
            "min_gap_s": 4.0}


def cfg_of(hcfg: dict) -> dict:
    c = dict(DEFAULTS)
    c.update(hcfg.get("opener") or {})
    return c


def to_highlight_time(t: float, segs: list[tuple[float, float]]) -> float | None:
    acc = 0.0
    for a, b in segs:
        if a <= t <= b:
            return acc + (t - a)
        acc += b - a
    return None


def pick_moments(points: list[tuple[float, float]], segs: list[tuple[float, float]], cfg: dict,
                 hook_s: float = 2.0, clip_s: float = 2.5) -> list[float]:
    """Початки уривків у часі хайлайту (порядок = порядок в opener'і), або [] якщо моментів < 3."""
    total = sum(b - a for a, b in segs)
    pre = float(cfg["pre_s"])
    cands = []
    for t, w in points:
        h = to_highlight_time(t, segs)
        if h is None:
            continue
        s = max(0.0, h - pre)
        if s + clip_s <= total:
            cands.append((w, t, s))
    cands.sort(key=lambda c: -c[0])
    chosen: list[tuple[float, float, float]] = []
    for c in cands:
        if all(abs(c[1] - x[1]) >= float(cfg["min_gap_s"]) for x in chosen):
            chosen.append(c)
        if len(chosen) >= max(3, min(6, int(cfg["moments"]))):
            break
    if len(chosen) < 3:
        return []
    peak, hook = chosen[0], chosen[1]
    middle = sorted(chosen[2:], key=lambda c: c[1])
    return [hook[2]] + [m[2] for m in middle] + [peak[2]]


def make_opener(settings: dict, cfg: dict, highlight_mp4: Path, starts: list[float], out: Path,
                size: tuple[int, int] | None = None) -> Path:
    tdir = (ROOT / cfg["template_dir"]).resolve()
    script = tdir / "fill_template.py"
    if not script.exists():
        raise FileNotFoundError(f"Немає шаблону opener'а: {script}")
    out.parent.mkdir(parents=True, exist_ok=True)
    job = out.with_suffix(".moments.json")
    canvas = {"width": size[0], "height": size[1]} if size else None
    job.write_text(json.dumps({"output": str(out), "captions": False, "canvas": canvas,
                               "moments": [{"src": str(highlight_mp4), "start": round(s, 3)} for s in starts]},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    env = dict(os.environ)
    ff_dir = str(Path(find_tool(settings, "ffmpeg")).parent)
    env["PATH"] = ff_dir + os.pathsep + env.get("PATH", "")
    env["PYTHONIOENCODING"] = "utf-8"       # Windows-консоль (cp1251) не вміє «→» та інші символи
    env["PYTHONUTF8"] = "1"
    r = subprocess.run([sys.executable, str(script), str(job)], cwd=str(tdir), env=env, capture_output=True,
                       creationflags=_NO_WINDOW)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError((r.stdout + r.stderr).decode("utf-8", "replace").strip()[-800:])
    job.unlink(missing_ok=True)
    return out


def join(settings: dict, opener: Path, main: Path, out: Path, width: int, height: int, fps: int) -> Path:
    """opener + хайлайт → один MP4 (opener уже зібраний у розмірі хайлайту; scale — про всяк випадок)."""
    ff = find_tool(settings, "ffmpeg")
    r = settings["render"]
    g = (f"[0:v]scale={width}:{height}:flags=lanczos,fps={fps},setsar=1,format=yuv420p[v0];"
         f"[0:a]aresample=48000,aformat=channel_layouts=stereo[a0];"
         f"[1:v]scale={width}:{height},fps={fps},setsar=1,format=yuv420p[v1];"
         f"[1:a]aresample=48000,aformat=channel_layouts=stereo[a1];"
         f"[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]")
    tmp = out.with_name(out.stem + ".part.mp4")
    err = ""
    # другий прохід (opener + хайлайт) — з запасом якості, щоб перекодування не «їло» картинку
    cq, crf = max(12, int(r["cq"]) - 3), max(12, int(r["x264_crf"]) - 3)
    maxrate = r["max_bitrate"]
    try:
        maxrate = f"{int(float(str(maxrate).rstrip('Mm')) * 1.5)}M"
    except ValueError:
        pass
    for enc in (r["encoder"], r["fallback_encoder"]):
        vc = (["-c:v", "h264_nvenc", "-preset", "p6", "-tune", "hq", "-rc", "vbr", "-cq", str(cq), "-b:v", "0",
               "-maxrate", maxrate, "-bufsize", "120M", "-profile:v", "high"] if enc == "h264_nvenc" else
              ["-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-profile:v", "high"])
        cmd = [ff, "-hide_banner", "-loglevel", "error", "-y", "-i", str(opener), "-i", str(main),
               "-filter_complex", g, "-map", "[v]", "-map", "[a]", *vc, "-c:a", "aac", "-b:a", r["audio_bitrate"],
               "-movflags", "+faststart", str(tmp)]
        res = subprocess.run(cmd, capture_output=True, creationflags=_NO_WINDOW)
        if res.returncode == 0 and tmp.exists():
            tmp.replace(out)
            return out
        err = res.stderr.decode("utf-8", "replace")[-500:]
    raise RuntimeError(err)
