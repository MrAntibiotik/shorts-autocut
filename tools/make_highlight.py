"""Хайлайт 16:10 будь-якого бою вже обробленого результату (незалежно від порогу 7000).

Запуск:
  extra\\make_highlight.bat                    — останній результат, вибір бою зі списку
  python tools/make_highlight.py <output\\job_…> <номер бою> [--json]
Telegram-бот викликає його кнопкою «🎬 Хайлайт цього бою». Результат — у <результат>\\highlights\\.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shorts.config import load_profile, load_settings, output_dir, work_dir  # noqa: E402
from shorts.events import Event  # noqa: E402
from shorts.logs import setup_logging  # noqa: E402
from shorts.media import probe  # noqa: E402
from shorts.recording import resolve_recording  # noqa: E402


def battles_of(job_dir: Path) -> list[dict]:
    """[{battle, rec, short, dir}] — бої результату, що мають папку зі звітом moments.json."""
    job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    out = []
    for rec in job.get("recordings", []):
        for sh in rec.get("shorts", []):
            d = job_dir / sh.get("dir", "")
            if (d / "moments.json").exists():
                out.append({"battle": sh["battle"], "rec": rec, "short": sh, "dir": d, "job": job})
    return out


def load_battle(d: Path) -> dict:
    data = json.loads((d / "moments.json").read_text(encoding="utf-8"))
    b = dict(data["бої"][0])
    b["events"] = [Event(e["t"], e["type"], e["source"], e.get("value"), e.get("confidence", 1.0),
                         e.get("detail", ""), e.get("extra") or {}) for e in b.pop("події", [])]
    return b


VIDEO_EXT = (".mp4", ".mkv", ".mov", ".flv")


def find_recording(path: str) -> Path:
    """Запис за шляхом з результату; якщо його перенесли/перейменували — той самий файл з іншим
    розширенням (OBS remux mkv→mp4) або з тією ж назвою в сусідніх папках."""
    p = Path(path)
    if p.exists():
        return p
    for ext in VIDEO_EXT:
        q = p.with_suffix(ext)
        if q.exists():
            return q
    for d in [p.parent, p.parent.parent, Path.home() / "Videos"]:
        try:
            for q in d.rglob(p.stem + ".*"):
                if q.suffix.lower() in VIDEO_EXT and "_webcam" not in q.stem:
                    return q
        except OSError:
            pass
    raise RuntimeError(f"Не знайдено запис гри: {p}\n(його перенесли, перейменували чи видалили? "
                       f"Поверніть файл на місце — тоді хайлайт вийде)")


def make(job_dir: Path, battle_idx: int, settings: dict | None = None) -> dict:
    from shorts.highlights import make_highlight
    settings = settings or load_settings()
    items = [x for x in battles_of(job_dir) if int(x["battle"]) == int(battle_idx)]
    if not items:
        raise RuntimeError(f"У {job_dir.name} немає бою {battle_idx} зі звітом (moments.json)")
    it = items[0]
    rec = it["rec"]
    orig_path = rec["path"]
    rec_path = find_recording(orig_path)
    rec = dict(rec, path=str(rec_path))
    info = probe(settings, rec_path)
    st = resolve_recording(settings, info)
    prof = copy.deepcopy(load_profile(it["job"].get("profile", "wot")))
    if rec.get("zones"):
        prof["zones"] = rec["zones"]
    if rec.get("mode"):
        prof["mode"] = rec["mode"]
    b = load_battle(it["dir"])
    mp4 = job_dir / "highlights" / f"{rec.get('tag', 'rec')}_{it['dir'].name}_highlight.mp4"
    start, end = (rec.get("range") or [0.0, info.duration])
    res = make_highlight(st, info, prof, b, rec.get("webcam"), mp4, work_dir(settings) / "jobs" / job_dir.name,
                         float(start), float(end))
    # запис у job.json — щоб бот і DaVinci знали про хайлайт
    job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    for r in job.get("recordings", []):
        if r.get("path") == orig_path:
            r["path"] = str(rec_path)                # запис знайшовся в іншому місці — запам'ятати
            hl = [h for h in r.get("highlights", []) if int(h.get("battle", -1)) != int(battle_idx)]
            r["highlights"] = hl + [res]
    (job_dir / "job.json").write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    return res


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("job_dir", nargs="?")
    ap.add_argument("battle", nargs="?", type=int)
    ap.add_argument("--json", action="store_true", help="вивести результат JSON (для бота)")
    a = ap.parse_args()
    settings = load_settings()
    setup_logging("make_highlight", work_dir(settings) / "logs")
    if a.job_dir:
        job_dir = Path(a.job_dir)
    else:
        jobs = sorted(p for p in output_dir(settings).glob("job_*") if (p / "job.json").exists())
        if not jobs:
            print("Результатів ще немає.")
            return 1
        job_dir = jobs[-1]
    battle = a.battle
    if battle is None:
        items = battles_of(job_dir)
        print(f"Результат: {job_dir.name}")
        for x in items:
            print(f"  {x['battle']:>3}. {x['short'].get('name', '')}")
        battle = int(input("Номер бою для хайлайту: ").strip())
    res = make(job_dir, battle, settings)
    if a.json:
        print("RESULT " + json.dumps(res, ensure_ascii=False))
    else:
        print(f"Готово: {res['mp4']} ({res['duration'] / 60:.1f} хв, opener: {'так' if res['opener'] else 'ні'})")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"[ПОМИЛКА] {e}")
        sys.exit(1)
