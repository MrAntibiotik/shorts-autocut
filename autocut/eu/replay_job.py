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

from autocut.eu.replay_meta import ReplayError, ReplayInfo, parse, title_en

log = logging.getLogger("replays")

PROFILE_ID = "wot_replay_en"
GAME_FOLDER = "WoT"
HASHTAGS = ["#worldoftanks", "#wot", "#tanks", "#gaming", "#shorts"]


@dataclass
class ReplayJobOptions:
    replays_dir: Path
    publish_dir: Path | None            # папка готових шортсів EN-каналу Poster; None — лише output
    cta_text: str = ""
    credit_author: bool = True          # «Replay by <нік>» в описі
    footer: str = ""                    # додатковий рядок в описі (Poster EN-каналу і так додає свій підпис)
    record: bool = True
    cut: bool = True
    only: list[str] = field(default_factory=list)   # конкретні файли реплеїв; порожньо — усі незаписані


def retry_failed(replays_dir: Path) -> int:
    """Знову в чергу реплеї, що не вдалися (прибирає позначки .failed). Повертає їх кількість."""
    n = 0
    for f in Path(replays_dir).glob("*.failed"):
        if f.with_suffix(".wotreplay").exists():
            f.unlink(missing_ok=True)
            n += 1
    return n


def cleanup_recordings(record_dir: Path, days: float = 3, now: float | None = None) -> tuple[int, int]:
    """Видаляє записи реплеїв (OBS) старші за days днів. Шортси, нарізки й .wotreplay не чіпає.
    Повертає (скільки файлів, скільки байтів). Викликати лише коли запис не йде."""
    import time as _t
    from autocut.core.recording import VIDEO_EXT
    if not days or days <= 0:
        return 0, 0
    d = Path(record_dir)
    if not d.is_dir():
        return 0, 0
    limit = (now or _t.time()) - float(days) * 86400
    n = size = 0
    for f in d.iterdir():
        try:
            if f.is_file() and f.suffix.lower() in VIDEO_EXT and f.stat().st_mtime < limit:
                sz = f.stat().st_size
                f.unlink()
                n, size = n + 1, size + sz
        except OSError as e:
            log.warning("Запис реплею не видалено (%s): %s", f.name, e)
    if n:
        log.info("Записи реплеїв старші за %g дн. видалено: %d (%.1f ГБ)", days, n, size / 1024 ** 3)
    return n, size


def problem_replays(settings: dict, replays_dir: Path) -> list[dict]:
    """Реплеї, з яких так і не вийшов шортс: [{rp, kind, reason, video}].
    kind: failed — не записався (повтор = записати знову); lost — запис зник (записати знову);
          uncut — запис є, шортса немає (повтор = лише нарізати з готового запису, без гри)."""
    shorts_by_video: dict[str, int] = {}
    try:
        for jf in replays_output_dir(settings).glob("job_*/job.json"):
            try:
                job = json.loads(jf.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for rec in job.get("recordings", []):
                k = str(Path(rec.get("path", "")).resolve())
                shorts_by_video[k] = max(shorts_by_video.get(k, 0), len(rec.get("shorts") or []))
    except OSError:
        pass
    out = []
    for rp in sorted(Path(replays_dir).glob("*.wotreplay")):
        failed, rec = rp.with_suffix(".failed"), rp.with_suffix(".recorded")
        if failed.exists():
            try:
                why = failed.read_text(encoding="utf-8").strip()
            except OSError:
                why = ""
            out.append({"rp": rp, "kind": "failed", "reason": why or "не записався", "video": None})
        elif rec.exists():
            try:
                video = Path(rec.read_text(encoding="utf-8").strip())
            except OSError:
                continue
            n = shorts_by_video.get(str(video.resolve()))
            if n:
                continue                      # шортс уже є — запис можна було видалити (прибирання за 3 дні)
            if not video.exists():
                out.append({"rp": rp, "kind": "lost", "reason": "файл запису зник — запишу знову", "video": None})
                continue
            out.append({"rp": rp, "kind": "uncut", "video": video,
                            "reason": "нарізка не знайшла моментів" if n == 0 else "запис є, нарізки не було"})
    return out


def prepare_retry(items: list[dict]) -> list[Path]:
    """Готує повтор: не записані / з втраченим записом — знову в чергу на запис; повертає ті, що треба
    лише нарізати (запис уже є)."""
    cut_only = []
    for it in items:
        rp = Path(it["rp"])
        if it["kind"] == "failed":
            rp.with_suffix(".failed").unlink(missing_ok=True)
        elif it["kind"] == "lost":
            rp.with_suffix(".recorded").unlink(missing_ok=True)
        elif it["kind"] == "uncut":
            cut_only.append(rp)
    return cut_only


def pending_replays(replays_dir: Path) -> list[Path]:
    """Реплеї в папці, які ще не оброблено (поруч немає <id>.recorded чи <id>.failed)."""
    return sorted(p for p in Path(replays_dir).glob("*.wotreplay")
                  if not p.with_suffix(".recorded").exists() and not p.with_suffix(".failed").exists())


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
            lines.append(f"Replay by {info.player}.")
        if opts.footer.strip():
            lines.append(opts.footer.strip())
        desc = "\n".join(lines)
    tags = list(HASHTAGS)
    line = " ".join(tags)
    return {
        "status": "ready_to_post",
        "game": GAME_FOLDER,
        "source": "replay",
        "channel": "en",            # лише для EN/EU-каналу: основний канал Poster такі шортси не бере
        "replay": info.to_dict() if info else None,
        "youtube": {"title": title, "description": desc, "tags": [t.lstrip("#") for t in tags], "hashtags": tags},
        "tiktok": {"caption": f"{title} {line}"},
        "instagram": {"caption": f"{title}\n\n{line}"},
    }


def target_dir(opts: ReplayJobOptions) -> Path | None:
    """Куди класти шортси з реплеїв: папка з налаштувань «Реплеїв» або (порожньо) — папка EN-каналу,
    яку повідомив Poster. Папку основного каналу — ніколи: це різний контент."""
    from autocut.eu.poster_link import channel_ready, is_main_channel_folder
    d = Path(opts.publish_dir) if opts.publish_dir else channel_ready("en")
    if d is None:
        return None
    if is_main_channel_folder(d):
        raise RuntimeError(f"«{d}» — папка ОСНОВНОГО каналу Poster. Шортси з реплеїв туди не кладу "
                           "(різний контент). Вкажіть у «Реплеях» папку EN-каналу або залиште поле порожнім.")
    return d


def publish(mp4: Path, info: ReplayInfo | None, opts: ReplayJobOptions, rid: str) -> Path | None:
    """Копіює шортс у папку EN-каналу Poster (атомарно: спершу .part, потім перейменування) + json."""
    root = target_dir(opts)
    if root is None:
        return None
    d = root / GAME_FOLDER
    d.mkdir(parents=True, exist_ok=True)
    name = f"replay_{rid}_{mp4.stem}"
    js = d / f"{name}.json"
    js.write_text(json.dumps(poster_json(info, opts), ensure_ascii=False, indent=2), encoding="utf-8")
    part = d / f"{name}.mp4.part"
    shutil.copy2(mp4, part)
    final = d / f"{name}.mp4"
    part.replace(final)
    _mark_published(mp4)
    return final


def _published_file(settings_out: Path) -> Path:
    return settings_out / "_published_en.json"


def _mark_published(mp4: Path) -> None:
    """Запам'ятати, що шортс уже віддано в Poster (щоб «догнати» лише невіддані)."""
    job_dir = next((p for p in Path(mp4).parents if p.name.startswith("job_")), None)
    if job_dir is None:
        return
    f = _published_file(job_dir.parent)
    try:
        done = set(json.loads(f.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        done = set()
    done.add(str(Path(mp4).resolve()))
    f.write_text(json.dumps(sorted(done), ensure_ascii=False, indent=1), encoding="utf-8")


def publish_unpublished(settings: dict, opts: ReplayJobOptions) -> list[Path]:
    """Шортси з реплеїв, які змонтувались, але не потрапили в Poster (папку EN-каналу не було вказано
    або Poster ще не повідомив її) — віддаємо зараз. Повертає нові шляхи в папці EN-каналу."""
    if target_dir(opts) is None:
        return []
    out_root = replays_output_dir(settings)
    try:
        done = set(json.loads(_published_file(out_root).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        done = set()
    videos = {}
    for marker in Path(opts.replays_dir).glob("*.recorded"):
        try:
            videos[str(Path(marker.read_text(encoding="utf-8").strip()).resolve())] = marker.with_suffix(".wotreplay")
        except OSError:
            continue
    out = []
    for jf in sorted(out_root.glob("job_*/job.json")):
        try:
            job = json.loads(jf.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for rec in job.get("recordings", []):
            rp = videos.get(str(Path(rec.get("path", "")).resolve()))
            for sh in rec.get("shorts", []):
                mp4 = Path(sh.get("mp4") or "")
                if not mp4.exists() or str(mp4.resolve()) in done:
                    continue
                info = info_of(rp) if rp and rp.exists() else None
                p = publish(mp4, info, opts, rp.stem if rp else jf.parent.name)
                if p:
                    log.info("Догнав у чергу EN-каналу: %s", p)
                    out.append(p)
    return out


def replays_output_dir(settings: dict) -> Path:
    """Окрема папка для шортсів з реплеїв (НЕ всередині output): основний Poster, який стежить за output,
    їх не бачить — вони йдуть лише в папку EN-каналу."""
    from autocut.core.config import resolve_path
    from autocut.core.content import LEGACY_EU, folder
    v = str((settings.get("paths") or {}).get("replays_output_dir") or "")
    d = folder(settings, "eu_cuts") if v in LEGACY_EU else resolve_path(v)   # …\\Контент\\EU-канал\\Нарізки
    d.mkdir(parents=True, exist_ok=True)
    mark = d / ".poster_channel"          # Poster основного каналу таку папку обходить (навіть без json у шортсах)
    if not mark.exists():
        try:
            mark.write_text("en", encoding="utf-8")
        except OSError:
            pass
    return d


def cut_recording(settings: dict, recording: Path, cta_text: str, progress=None, cancel=None) -> list[Path]:
    """Автонарізка одного запису реплею профілем wot_replay_en. Повертає готові MP4."""
    from autocut.core.pipeline import Job, JobOptions, Progress
    st = copy.deepcopy(settings)
    st.setdefault("paths", {})["output_dir"] = str(replays_output_dir(settings))   # не в загальний output
    jopts = JobOptions(recordings=[recording], profile_id=PROFILE_ID, cta_text=cta_text, render=True,
                       separation=False, subtitles=False, speech_ai=False, highlights=False)
    import threading

    from autocut.core.resolve_export import write_status
    info = {"stage": "Нарізка реплею", "percent": 0}
    stop = threading.Event()

    def beat():                       # «пульс» для «⏳ Статус» у боті: обробка жива, хоч етап і довгий
        while not stop.wait(10):
            try:
                write_status(settings, stage=info["stage"], percent=info["percent"])
            except Exception:  # noqa: BLE001
                pass

    def cb(stage, frac):
        info["stage"], info["percent"] = stage, int(frac * 100)
        if progress:
            progress(stage, frac)
    threading.Thread(target=beat, daemon=True, name="cut-heartbeat").start()
    try:
        res = Job(st, jopts, Progress(callback=cb, cancel_flag=cancel)).run()
    finally:
        stop.set()
    out = []
    for rec in res.get("recordings", []):
        for sh in rec.get("shorts", []):
            if sh.get("mp4") and Path(sh["mp4"]).exists():
                out.append(Path(sh["mp4"]))
    return out


def run(settings: dict, opts: ReplayJobOptions, rec_opts=None, progress=None, cancel=None, who: str = "вкладка",
        on_short=None, on_stage=None) -> dict:
    """Записує й нарізає реплеї з папки. progress(текст, частка); cancel() -> bool;
    on_short(шлях у Poster, дані реплею) — після кожного готового шортса;
    on_stage(реплей | None, етап, стан, деталь) — для живих карток у боті:
      етапи obs / rec / cut / post; стани run / done / err / skip (реплей None — стосується всіх).
    Busy (replay_state), якщо запис уже йде з іншого місця (вкладка / бот)."""
    from autocut.eu.replay_state import running
    replays = [Path(p) for p in opts.only] if opts.only else pending_replays(opts.replays_dir)
    summary = {"replays": len(replays), "recorded": 0, "shorts": [], "published": [], "errors": []}
    try:   # змонтовані раніше, але не віддані в Poster — у чергу EN-каналу
        summary["published"] += [str(p) for p in publish_unpublished(settings, opts)]
    except Exception as e:  # noqa: BLE001
        log.warning("Догнати невіддані шортси: %s", e)
        summary["errors"].append(str(e))
    if not replays:
        return summary
    with running(opts.replays_dir, who) as alive:
        _run(settings, opts, rec_opts, replays, summary, progress, cancel, alive, on_short, _stage_cb(on_stage))
    return summary


def _stage_cb(on_stage):
    def call(rp, stage, state, detail=""):
        if on_stage:
            try:
                on_stage(rp, stage, state, detail)
            except Exception as e:  # noqa: BLE001 — сповіщення не має ламати запис
                log.warning("Сповіщення про етап: %s", e)
    return call


def _mmss(sec: float) -> str:
    sec = max(0, int(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def _run(settings, opts, rec_opts, replays, summary, progress, cancel, alive, on_short, stage):
    from autocut.eu.replay_record import RecordError, ensure_obs, game_exe
    obs = None
    if opts.record:
        stage(None, "obs", "run", "перевіряю гру й OBS…")
        try:
            game_exe(rec_opts.game_dir)    # немає гри — зупиняємось ДО запису: реплеї лишаються в черзі
            obs = ensure_obs(rec_opts)     # OBS не відкритий — запуститься сам
        except RecordError as e:
            stage(None, "obs", "err", str(e))
            raise
        stage(None, "obs", "done", "OBS готовий" + (" (запущено програмою)" if obs.launched else ""))
    try:
        _run_all(settings, opts, rec_opts, replays, summary, progress, cancel, alive, on_short, obs, stage)
    finally:
        if obs is not None:
            obs.restore()                                     # профіль / набір сцен — як були
            from autocut.eu.replay_record import close_obs
            if not pending_replays(opts.replays_dir):       # черга порожня — OBS, відкритий програмою, закриваємо
                if close_obs():
                    stage(None, "obs", "done", "OBS закрито після запису")


def _run_all(settings, opts, rec_opts, replays, summary, progress, cancel, alive, on_short, obs, stage):
    from autocut.eu.replay_record import record_one, record_seconds
    for i, rp in enumerate(replays):
        alive()
        if cancel and cancel():
            break
        rid = rp.stem
        info = info_of(rp)

        def sub(text, frac, i=i):
            if progress:
                progress(f"[{i + 1}/{len(replays)}] {text}", (i + max(0.0, min(1.0, frac))) / len(replays))
        marker = rp.with_suffix(".recorded")
        cur = "rec" if opts.record else "cut"
        try:
            if opts.record:
                total = record_seconds(info, rec_opts)
                stage(rp, "rec", "run", f"0:00 / ~{_mmss(total)}")

                def rec_progress(t, f, total=total):
                    sub(t, f * 0.6)
                    rec = t.split("OBS ", 1)[1] if "OBS " in t else ""     # лічильник самого OBS — запис справді йде
                    stage(rp, "rec", "run", f"{_mmss(f * total)} / ~{_mmss(total)}" + (f" · {rec}" if rec else ""))
                def on_camera(state, text, rp=rp):
                    shown = f"«{text[:80]}»" if text else "напису не видно"
                    if state == "player":
                        stage(rp, "cam", "done", f"від гравця · {shown}")
                    elif state == "free":
                        stage(rp, "cam", "run", f"вільна — клікаю ще раз · {shown}")
                    else:
                        stage(rp, "cam", "skip", f"напис не розпізнано · {shown}")
                video = record_one(rp, info, rec_opts, obs, progress=rec_progress, cancel=cancel,
                                   on_camera=on_camera)
                marker.write_text(str(video), encoding="utf-8")
                summary["recorded"] += 1
                stage(rp, "rec", "done", Path(video).name)
            elif marker.exists():
                video = Path(marker.read_text(encoding="utf-8").strip())
            else:
                raise RuntimeError("реплей ще не записано")
            if opts.cut:
                cur = "cut"
                sub("Автонарізка…", 0.6)
                stage(rp, "cut", "run", "шукаю моменти…")
                shorts = cut_recording(settings, video, opts.cta_text,
                                       progress=lambda st_, f: (sub(st_, 0.6 + 0.4 * f),
                                                                stage(rp, "cut", "run", f"{int(f * 100)}%")), cancel=cancel)
                summary["shorts"] += [str(s) for s in shorts]
                if not shorts:
                    log.info("%s: шортсів немає (у бою не знайдено моментів)", rp.name)
                    stage(rp, "cut", "skip", "у бою не знайдено моментів — шортса немає")
                    stage(rp, "post", "skip", "")
                else:
                    stage(rp, "cut", "done", f"шортсів: {len(shorts)}")
                cur = "post"
                for s in shorts:
                    p = publish(s, info, opts, rid)
                    if p:
                        summary["published"].append(str(p))
                        log.info("  → у публікацію: %s", p)
                    stage(rp, "post", "done" if p else "skip",
                          Path(p).name if p else "папки EN-каналу немає (запустіть Poster або вкажіть її в «Реплеях») — "
                                                 "шортс в output_replays_en, віддам у чергу при наступному запуску")
                    if on_short:
                        try:
                            on_short(p or s, info)
                        except Exception as e:  # noqa: BLE001 — сповіщення не має ламати запис
                            log.warning("Сповіщення про шортс: %s", e)
        except Exception as e:  # noqa: BLE001 — один невдалий реплей не зупиняє решту
            if type(e).__name__ == "Cancelled":
                raise
            log.error("%s: %s", rp.name, e)
            summary["errors"].append(f"{rp.name}: {e}")
            stage(rp, cur, "err", str(e))
            if not marker.exists():   # не записався — не пробувати знову щоразу (видаліть .failed, щоб повторити)
                rp.with_suffix(".failed").write_text(str(e), encoding="utf-8")


__all__ = ["PROFILE_ID", "ReplayJobOptions", "retry_failed", "cleanup_recordings", "problem_replays", "prepare_retry", "cut_recording", "pending_replays", "poster_json", "publish",
           "publish_unpublished", "target_dir", "run"]
