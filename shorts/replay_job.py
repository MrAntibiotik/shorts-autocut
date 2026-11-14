"""Повний цикл для реплеїв: скачати → записати (гра + OBS) → нарізати → покласти в папку EN-каналу Poster.

Кожен готовий шортс кладеться в <папка публікації>/WoT/ разом із json для Poster:
назва й опис англійською з даних реплею («Leopard1 — 7,412 damage on Prokhorovka 🔥»),
хештеги, статус ready_to_post. Далі Poster EN-каналу сам ставить шортс у розклад.
"""

from __future__ import annotations

import copy
import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .replay_meta import ReplayError, ReplayInfo, parse, title_en

log = logging.getLogger("replays")

PROFILE_ID = "wot_replay_en"
GAME_FOLDER = "WoT"
HASHTAGS = ["#worldoftanks", "#wot", "#tanks", "#gaming", "#shorts"]


@dataclass
class ReplayJobOptions:
    replays_dir: Path
    publish_dir: Path | None            # папка готових шортсів EN-каналу Poster; None — лише output
    cta_text: str = ""
    credit_author: bool = True          # «Replay by <нік> via wotreplays» в описі
    footer: str = ""                    # додатковий рядок в описі (Poster EN-каналу і так додає свій підпис)
    record: bool = True
    cut: bool = True
    only: list[str] = field(default_factory=list)   # конкретні файли реплеїв; порожньо — усі незаписані


def pending_replays(replays_dir: Path) -> list[Path]:
    """Реплеї в папці, які ще не записано (поруч немає <id>.recorded)."""
    return sorted(p for p in Path(replays_dir).glob("*.wotreplay") if not p.with_suffix(".recorded").exists())


def info_of(replay: Path) -> ReplayInfo | None:
    try:
        return parse(replay)
    except ReplayError as e:
        log.warning("%s: %s", replay.name, e)
        return None


def poster_json(info: ReplayInfo | None, opts: ReplayJobOptions) -> dict:
    """json для Poster (формат generate_meta.py)."""
    if info is None:
        title, desc = "World of Tanks — insane moment 🔥", "Best World of Tanks moments."
    else:
        title = title_en(info)
        lines = [f"{info.vehicle} on {info.map_name}: {info.stats_line}."]
        if info.won is not None:
            lines[0] += " Victory!" if info.won else ""
        if opts.credit_author and info.player:
            lines.append(f"Replay by {info.player} via wotreplays.")
        if opts.footer.strip():
            lines.append(opts.footer.strip())
        desc = "\n".join(lines)
    tags = list(HASHTAGS)
    line = " ".join(tags)
    return {
        "status": "ready_to_post",
        "game": GAME_FOLDER,
        "source": "replay",
        "replay": info.to_dict() if info else None,
        "youtube": {"title": title, "description": desc, "tags": [t.lstrip("#") for t in tags], "hashtags": tags},
        "tiktok": {"caption": f"{title} {line}"},
        "instagram": {"caption": f"{title}\n\n{line}"},
    }


def publish(mp4: Path, info: ReplayInfo | None, opts: ReplayJobOptions, rid: str) -> Path | None:
    """Копіює шортс у папку EN-каналу Poster (атомарно: спершу .part, потім перейменування) + json."""
    if not opts.publish_dir:
        return None
    d = Path(opts.publish_dir) / GAME_FOLDER
    d.mkdir(parents=True, exist_ok=True)
    name = f"replay_{rid}_{mp4.stem}"
    js = d / f"{name}.json"
    js.write_text(json.dumps(poster_json(info, opts), ensure_ascii=False, indent=2), encoding="utf-8")
    part = d / f"{name}.mp4.part"
    shutil.copy2(mp4, part)
    final = d / f"{name}.mp4"
    part.replace(final)
    return final


def cut_recording(settings: dict, recording: Path, cta_text: str, progress=None, cancel=None) -> list[Path]:
    """Автонарізка одного запису реплею профілем wot_replay_en. Повертає готові MP4."""
    from .pipeline import Job, JobOptions, Progress
    st = copy.deepcopy(settings)
    jopts = JobOptions(recordings=[recording], profile_id=PROFILE_ID, cta_text=cta_text, render=True,
                       separation=False, subtitles=False, speech_ai=False, highlights=False)
    res = Job(st, jopts, Progress(callback=progress, cancel_flag=cancel)).run()
    out = []
    for rec in res.get("recordings", []):
        for sh in rec.get("shorts", []):
            if sh.get("mp4") and Path(sh["mp4"]).exists():
                out.append(Path(sh["mp4"]))
    return out


def run(settings: dict, opts: ReplayJobOptions, rec_opts=None, progress=None, cancel=None) -> dict:
    """Записує й нарізає реплеї з папки. progress(текст, частка); cancel() -> bool."""
    from .replay_record import Obs, record_one
    replays = [Path(p) for p in opts.only] if opts.only else pending_replays(opts.replays_dir)
    summary = {"replays": len(replays), "recorded": 0, "shorts": [], "published": [], "errors": []}
    if not replays:
        return summary
    obs = Obs(rec_opts.obs_host, rec_opts.obs_port, rec_opts.obs_password) if opts.record else None
    for i, rp in enumerate(replays):
        if cancel and cancel():
            break
        rid = rp.stem
        info = info_of(rp)

        def sub(text, frac, i=i):
            if progress:
                progress(f"[{i + 1}/{len(replays)}] {text}", (i + max(0.0, min(1.0, frac))) / len(replays))
        marker = rp.with_suffix(".recorded")
        try:
            if opts.record:
                video = record_one(rp, info, rec_opts, obs, progress=lambda t, f: sub(t, f * 0.6), cancel=cancel)
                marker.write_text(str(video), encoding="utf-8")
                summary["recorded"] += 1
            elif marker.exists():
                video = Path(marker.read_text(encoding="utf-8").strip())
            else:
                raise RuntimeError("реплей ще не записано")
            if opts.cut:
                sub("Автонарізка…", 0.6)
                shorts = cut_recording(settings, video, opts.cta_text,
                                       progress=lambda stage, f: sub(stage, 0.6 + 0.4 * f), cancel=cancel)
                summary["shorts"] += [str(s) for s in shorts]
                if not shorts:
                    log.info("%s: шортсів немає (у бою не знайдено моментів)", rp.name)
                for s in shorts:
                    p = publish(s, info, opts, rid)
                    if p:
                        summary["published"].append(str(p))
                        log.info("  → у публікацію: %s", p)
        except Exception as e:  # noqa: BLE001 — один невдалий реплей не зупиняє решту
            if type(e).__name__ == "Cancelled":
                raise
            log.error("%s: %s", rp.name, e)
            summary["errors"].append(f"{rp.name}: {e}")
    return summary


__all__ = ["PROFILE_ID", "ReplayJobOptions", "cut_recording", "pending_replays", "poster_json", "publish", "run"]
