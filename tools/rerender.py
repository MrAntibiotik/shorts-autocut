"""Повторний рендер MP4 після ручних правок субтитрів (SRT) або тексту заклику.

Як користуватися:
  1. У папці бою (output\\job_...\\battle_03) змініть Блокнотом заклик у файлі cta.txt
     (порожній = без заклику; можна емодзі 😱🔥). Субтитри (якщо ввімкнені) — у файлі .srt там само.
  2. Перетягніть ПАПКУ результату на extra\\rerender.bat (або запустіть його — візьме останній результат).
  3. Вкажіть номери шортсів (напр. «3» або «1,4») або Enter — усі.
Таймлайни в DaVinci це не змінює — там правте текст прямо на таймлайні.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shorts.config import load_settings, output_dir, work_dir  # noqa: E402
from shorts.errors import UserError  # noqa: E402
from shorts.logs import setup_logging  # noqa: E402
from shorts.media import probe  # noqa: E402
from shorts.pipeline import build_overlays, layout_from_dict  # noqa: E402
from shorts.render import render_short  # noqa: E402
from shorts.subtitles import read_srt  # noqa: E402


def short_paths(job_dir: Path, sh: dict) -> tuple[Path, Path, Path]:
    """(srt, cta.txt, mp4) шортсу."""
    base = sh["file_base"]
    sdir = job_dir / sh.get("dir", "")
    srt = Path(sh["srt"]) if sh.get("srt") else sdir / f"{base}.srt"
    cta_txt = Path(sh["cta_txt"]) if sh.get("cta_txt") else sdir / "cta.txt"
    mp4 = Path(sh["mp4"]) if sh.get("mp4") else sdir / f"{base}.mp4"
    return srt, cta_txt, mp4


def rerender_short(settings: dict, job: dict, job_dir: Path, rec: dict, sh: dict, log=print) -> Path:
    """Перерендер одного шортсу з поточними .srt і cta.txt у його папці."""
    info = probe(settings, rec["path"])
    layout = layout_from_dict(rec["layout"])
    srt, cta_txt, mp4 = short_paths(job_dir, sh)
    cues = read_srt(srt) if srt.exists() else []
    text = cta_txt.read_text(encoding="utf-8").strip() if cta_txt.exists() else ""
    cta = None
    if text:
        d = min(float(settings["cta"]["duration_s"]), sh["duration"])
        cta = {"text": text, "start": sh["duration"] - d, "dur": d}
    tmp = work_dir(settings) / "jobs" / job["job_id"] / "rerender" / sh["file_base"]
    overlays = build_overlays(settings, cues, cta, layout, tmp)
    log(f"Рендерю {sh['file_base']} ({len(cues)} субтитрів{', із закликом' if cta else ''})…")
    render_short(settings, info, layout, [(s["s"], s["e"]) for s in sh["segments"]], overlays, mp4,
                 rec.get("webcam"))
    return mp4


def main() -> int:
    settings = load_settings()
    setup_logging("rerender", work_dir(settings) / "logs")
    if len(sys.argv) > 1:
        job_dir = Path(sys.argv[1].strip('"'))
    else:
        jobs = sorted(output_dir(settings).glob("job_*"))
        if not jobs:
            raise UserError("Ще немає жодного результату обробки.", "Спершу запустіть обробку.")
        job_dir = jobs[-1]
    job_file = job_dir / "job.json"
    if not job_file.exists():
        raise UserError(f"У папці {job_dir} немає job.json.", "Перетягніть на extra\\rerender.bat саму папку output\\job_….")
    job = json.loads(job_file.read_text(encoding="utf-8"))
    print(f"Результат: {job_dir}")
    all_shorts = [(rec, sh) for rec in job["recordings"] for sh in rec["shorts"]]
    for i, (rec, sh) in enumerate(all_shorts, 1):
        print(f"  {i}. {sh['name']}  ({sh['duration']:.0f} с)")
    ans = input("Які перерендерити? (номери через кому, Enter = усі): ").strip()
    pick = set(range(1, len(all_shorts) + 1))
    if ans:
        pick = {int(x) for x in ans.replace(" ", "").split(",") if x.isdigit()}

    for i, (rec, sh) in enumerate(all_shorts, 1):
        if i in pick:
            rerender_short(settings, job, job_dir, rec, sh, log=print)
    print("Готово.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except UserError as e:
        print(f"\n[ПОМИЛКА] {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(1)
