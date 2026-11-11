"""ЕКСПЕРИМЕНТ: перевірка розумного розпізнавання мови на відрізку запису. Нічого не змінює.

Найпростіше: перетягніть запис на extra\\test_speech.bat і дайте відповіді на питання.
Командний рядок:
  python tools/test_speech.py ЗАПИС.mp4 --start 14:00 --end 18:00 [--no-separation]

Результат (output\\reports):
  *_speech.txt — фрази з часом, порівняння старого (за гучністю) і нового (Silero) визначення мови;
  *_speech.srt — субтитри всього відрізка (відкрийте разом із записом у плеєрі, напр. VLC / MPC);
  *_speech.json — усі слова з часом і впевненістю.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from shorts.audio import AudioPrep  # noqa: E402
from shorts.config import load_profile, load_settings, output_dir, work_dir  # noqa: E402
from shorts.errors import UserError  # noqa: E402
from shorts.logs import setup_logging  # noqa: E402
from shorts.media import probe  # noqa: E402
from shorts.selection import SpeechMap  # noqa: E402
from shorts import speech_ai  # noqa: E402
from shorts.util import fmt_time, parse_time  # noqa: E402

log = logging.getLogger("test_speech")


def ask(prompt: str, default: str = "") -> str:
    d = f" [Enter = {default}]" if default else ""
    return input(f"{prompt}{d}: ").strip() or default


def srt_t(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def runs(parts: list[tuple[float, np.ndarray]], step: float) -> list[tuple[float, float]]:
    out = []
    for c0, m in parts:
        i = 0
        while i < len(m):
            if m[i]:
                j = i
                while j < len(m) and m[j]:
                    j += 1
                out.append((c0 + i * step, c0 + j * step))
                i = j
            else:
                i += 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Перевірка розумного розпізнавання мови")
    ap.add_argument("source", nargs="?")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--no-separation", action="store_true")
    ap.add_argument("--ask", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    settings = load_settings()
    log_file = setup_logging("test_speech", work_dir(settings) / "logs", args.verbose)
    ok, why = speech_ai.available()
    if not ok:
        raise UserError(f"Бібліотеки не встановлено ({why}). Спершу запустіть extra\\setup_speech_ai.bat.")
    cfg = speech_ai.cfg_of(settings)
    src = Path(args.source or input("Перетягніть сюди файл запису і натисніть Enter: ").strip().strip('"'))
    info = probe(settings, src)
    from shorts.recording import resolve_recording
    settings = resolve_recording(settings, info)
    start, end = parse_time(args.start) or 0.0, parse_time(args.end)
    if args.ask or args.start is None:
        print(f"\nЗапис: {info.path.name}, тривалість {fmt_time(info.duration, 0)}.")
        print("Візьміть відрізок 2–10 хв, де ви багато говорите (напр. бій).")
        start = parse_time(ask("З якого моменту (хв:сек)", "0")) or 0.0
        e = ask("До якого моменту", fmt_time(min(info.duration, start + 300), 0))
        end = parse_time(e)
    end_t = info.duration if end is None else min(end, info.duration)

    t_all = time.time()
    print(f"\n[1/3] Підготовка звуку {fmt_time(start, 0)} – {fmt_time(end_t, 0)}…")
    chunks = AudioPrep(settings, load_profile("wot"), info, force_mix=args.no_separation).prepare(start, end_t)

    print("[2/3] Межі мови: за гучністю (як зараз) і Silero VAD (новий спосіб)…")
    old = SpeechMap(chunks, load_profile("wot")["selection"]["vad_margin_db"])
    t0 = time.time()
    new_parts = speech_ai.silero_parts(chunks, cfg)
    t_vad = time.time() - t0
    old_runs, new_runs = runs(old.parts, SpeechMap.STEP), runs(new_parts, speech_ai.VAD_STEP)

    print(f"[3/3] Розпізнаю мову (faster-whisper, {cfg.get('model')}, {cfg.get('device')})…")
    t0 = time.time()
    words = []
    win = 120.0
    a = start
    while a < end_t:                      # шматками по 2 хв — пам'ять не росте
        b = min(end_t, a + win)
        words += speech_ai.transcribe_audio(speech_ai.voice_audio(chunks, a, b), cfg, offset=a)
        print(f"    {fmt_time(b - start, 0)} / {fmt_time(end_t - start, 0)}")
        a = b
    t_asr = time.time() - t0
    words, dropped = speech_ai.filter_phrases(words, cfg)
    phrases = speech_ai.phrases_of(words, float(cfg.get("phrase_gap_s", 0.7)))

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    rep = output_dir(settings) / "reports"
    rep.mkdir(parents=True, exist_ok=True)
    base = rep / f"{src.stem}_speech_{stamp}"
    dur = end_t - start
    old_s = sum(b - a for a, b in old_runs)
    new_s = sum(b - a for a, b in new_runs)
    lines = [
        f"Запис: {src}", f"Відрізок: {fmt_time(start)} – {fmt_time(end_t)} ({fmt_time(dur, 0)})",
        f"Модель: {cfg.get('model')} на {cfg.get('device')}/{cfg.get('compute_type')}",
        f"Швидкість: Silero VAD {t_vad:.1f} с, розпізнавання {t_asr:.1f} с (×{dur / max(t_asr, 0.1):.1f} від реального часу)",
        "",
        "МЕЖІ МОВИ",
        f"  за гучністю (зараз): {len(old_runs)} шматків мови, разом {old_s:.0f} с",
        f"  Silero VAD (новий):  {len(new_runs)} шматків мови, разом {new_s:.0f} с",
        "  (Менше дрібних шматків і без шуму/гри — краще. Порівняйте з тим, що чуєте у записі.)",
        "",
        f"ФРАЗИ ({len(phrases)}), слів: {len(words)}",
    ]
    for p in phrases:
        lines.append(f"  {fmt_time(p.start, 1):>9} – {fmt_time(p.end, 1):<9} {p.text}")
    lines += ["", f"ВІДКИНУТО ({len(dropped)}) — голос гри, вигадки моделі, невпевнене (у субтитри не підуть):"]
    lines += [f"  {fmt_time(p.start, 1):>9}  {p.text}   — {why}" for p, why in dropped]
    lines += ["  (Відкинуто вашу фразу або пропущено фразу гри — напишіть, підкрутимо speech_ai.game_phrases"
              " / min_phrase_prob у settings.json.)"]
    lines += ["", "Слова з низькою впевненістю (< 0.5), можливі помилки:"]
    lines += [f"  {fmt_time(w.start, 1)}  {w.text}  ({w.prob:.2f})" for w in words if w.prob < 0.5][:100]
    txt = base.with_suffix(".txt")
    txt.write_text("\n".join(lines) + "\n", encoding="utf-8")

    srt = []
    for i, p in enumerate(phrases, 1):
        srt += [str(i), f"{srt_t(p.start)} --> {srt_t(p.end)}", p.text, ""]
    srt_p = base.with_suffix(".srt")
    srt_p.write_text("\n".join(srt), encoding="utf-8")
    js = base.with_suffix(".json")
    js.write_text(json.dumps({"запис": str(src), "start": start, "end": end_t,
                              "слова": [w.__dict__ for w in words],
                              "мова_за_гучністю": old_runs, "мова_silero": new_runs},
                             ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n" + "\n".join(lines[:12]))
    for p in phrases[:15]:
        print(f"  {fmt_time(p.start, 1):>9}  {p.text}")
    if len(phrases) > 15:
        print(f"  … ще {len(phrases) - 15} — у файлі")
    print(f"\nГотово за {fmt_time(time.time() - t_all, 0)}.")
    print(f"Звіт:      {txt}\nСубтитри:  {srt_p}  (час — від початку ЗАПИСУ; у плеєрі відкрийте з записом)"
          f"\nСлова:     {js}\nЛог:       {log_file}")
    return 0


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    try:
        sys.exit(main())
    except UserError as e:
        print(f"\n[ПОМИЛКА] {e}")
        sys.exit(1)
