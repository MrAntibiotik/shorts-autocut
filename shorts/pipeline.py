"""Повний цикл обробки запису(ів): моменти -> бої -> відбір -> субтитри -> MP4 -> дані для DaVinci.

Викликається з вікна запуску (tools/run_job.py), яке відкриває кнопка в DaVinci,
або з командного рядка. Результат — папка output/job_<дата>/:
  short_01.mp4, short_01.srt, ...      готові шортси + субтитри
  report.csv / report.json             звіт: бій, таймкоди, тип, шкода, score, джерело, кліп
  <запис>_markers.edl                  маркери всіх моментів для DaVinci
  job.json                             усе для повторного рендеру (extra\\rerender.bat)
  job_result.lua                       дані для Lua-кнопки в DaVinci
"""

from __future__ import annotations

import copy
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import events as E
from .audio import AudioPrep
from .audio_detector import GameSoundDetector
from .cleanup import cleanup_work
from .config import load_profile, output_dir, work_dir
from .errors import UserError
from .fusion import assign_to_battles, fuse, split_by_gaps
from .layout import Layout, compute_layout
from .media import probe
from .render import Overlay, render_short
from .report import write_reports
from .resolve_export import markers_for, write_lua, write_marker_edl, write_status
from .selection import Short, SpeechMap, describe, select_shorts
from .recording import find_webcam_file, resolve_recording, webcam_offset  # noqa: F401
from .speech_ai import words_for_short
from .subtitles import Cue, add_emoji, build_cues, transcribe_shorts, write_srt
from .textimg import render_text
from .util import fmt_time
from .voice_detector import VoiceDetector

log = logging.getLogger("pipeline")


class Cancelled(Exception):
    pass


@dataclass
class JobOptions:
    recordings: list[Path]
    profile_id: str
    cta_text: str = ""
    start: float | None = None
    end: float | None = None
    render: bool = True
    subtitles: bool = False
    speech_ai: bool = False            # експериментально: Silero VAD + faster-whisper
    modes: dict = field(default_factory=dict)   # {шлях запису: "damage" | "assist"} — режим гри (WoT)
    separation: bool = True


@dataclass
class Progress:
    """Прогрес усього завдання: етап, частка 0..1, повідомлення."""
    callback: object = None
    cancel_flag: object = None     # callable -> bool
    stage: str = ""
    base: float = 0.0
    span: float = 1.0

    def set_stage(self, name: str, base: float, span: float):
        self.stage, self.base, self.span = name, base, span
        self.report(0.0)
        log.info("— %s", name)

    def report(self, frac: float):
        if self.cancel_flag and self.cancel_flag():
            raise Cancelled()
        if self.callback:
            self.callback(self.stage, min(1.0, self.base + self.span * max(0.0, min(1.0, frac))))

    def sub(self, a: float, b: float):
        """Колбек для частини етапу [a, b]."""
        return lambda f: self.report(a + (b - a) * f)


TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e", "є": "ie", "ж": "zh", "з": "z",
    "и": "y", "і": "i", "ї": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p",
    "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh",
    "щ": "shch", "ь": "", "ю": "iu", "я": "ia", "ё": "e", "ы": "y", "э": "e", "ъ": ""})


def ascii_name(s: str, maxlen: int = 40) -> str:
    t = s.lower().translate(TRANSLIT)
    t = re.sub(r"[^a-z0-9]+", "_", t).strip("_")
    return (t or "rec")[:maxlen]


class Job:
    def __init__(self, settings: dict, opts: JobOptions, progress: Progress | None = None):
        self.settings = settings
        self.opts = opts
        self.progress = progress or Progress()
        self.profile = load_profile(opts.profile_id)
        self.job_id = datetime.now().strftime("job_%Y%m%d_%H%M%S")
        self.out = output_dir(settings) / self.job_id
        self.work = work_dir(settings) / "jobs" / self.job_id
        self.result: dict = {}

    # ------------------------------------------------------------------ головне
    def run(self) -> dict:
        self.out.mkdir(parents=True, exist_ok=True)
        self.work.mkdir(parents=True, exist_ok=True)
        if not self.opts.separation:
            self.settings["separation"]["enabled"] = False
        write_status(self.settings, status="running", job_id=self.job_id, job_dir=str(self.out),
                     started=int(time.time()), stage="старт", percent=0, message="")
        t0 = time.time()
        recs = []
        n = len(self.opts.recordings)
        for i, rec in enumerate(self.opts.recordings):
            recs.append(self._process_recording(Path(rec), i, n))
        total = sum(len(r["shorts"]) for r in recs)
        self.result = {
            "status": "done", "job_id": self.job_id, "job_dir": str(self.out),
            "profile": self.opts.profile_id, "profile_name": self.profile["name"],
            "folder_name": f"Шортси {datetime.now():%Y-%m-%d %H-%M}",
            "timeline": {"width": self.profile["layout"]["output"]["width"],
                         "height": self.profile["layout"]["output"]["height"],
                         "fps": self.profile["layout"]["output"]["fps"]},
            "resolve": self.settings["resolve"],
            "cta_style": {"font_size": self.settings["cta"]["font_size"],
                          "y": self.profile["layout"].get("cta_y") or self.settings["cta"]["y"],
                          "duration": self.settings["cta"]["duration_s"]},
            "recordings": recs,
            "elapsed_s": round(time.time() - t0, 1),
        }
        write_lua(self.out / "job_result.lua", self.result)
        (self.out / "job.json").write_text(json.dumps(self.result, ensure_ascii=False, indent=2), encoding="utf-8")
        write_status(self.settings, status="done", stage="готово", percent=100,
                     result_file=str(self.out / "job_result.lua"),
                     message=f"Готово: {total} шортсів за {fmt_time(time.time() - t0, 0)}")
        log.info("ГОТОВО: %d шортсів за %s. Папка: %s", total, fmt_time(time.time() - t0, 0), self.out)
        cleanup_work(self.settings, keep=[self.work])
        return self.result

    # ------------------------------------------------------------------ експериментально: розумна мова
    def _silero_speech(self, chunks, cfg: dict, fallback: SpeechMap) -> SpeechMap:
        if not cfg.get("use_silero_vad", True):
            return fallback
        try:
            from .speech_ai import silero_parts
            sm = SpeechMap.from_parts(silero_parts(chunks, cfg))
            log.info("  [Експеримент] Межі мови — Silero VAD")
            return sm
        except Exception as e:
            log.warning("[УВАГА] Silero VAD не спрацював (%s) — межі мови за гучністю, як раніше. "
                        "Перевірте extra\\setup_speech_ai.bat", e)
            return fallback

    def _speech_ai(self, chunks, shorts, battles, cfg: dict, start: float, end: float, P):
        """Слова з часом (faster-whisper) навколо моментів + нарізка за реченнями.
        Помилка — попередження і звичайна робота без цього."""
        try:
            from .speech_ai import SpeechAI, extend_to_phrases, phrases_of, windows_for
            used = {sh.battle for sh in shorts}
            wins = windows_for([b for b in battles if b["index"] in used], cfg, start, end)
            log.info("  [Експеримент] Розпізнаю мову навколо моментів: %d вікон, %.0f с звуку",
                     len(wins), sum(b - a for a, b in wins))
            cache = Path(chunks[0].voice).parent.parent
            words = SpeechAI(self.settings, cache).words(chunks, wins)
            log.info("  [Експеримент] Розпізнано слів: %d", len(words))
            if cfg.get("sentence_cut", True) and words:
                phrases = phrases_of(words, float(cfg.get("phrase_gap_s", 0.7)))
                sel = self.profile["selection"]
                c = dict(cfg, merge_gap_s=sel["merge_gap_s"])
                for sh in shorts:
                    before = sh.duration
                    sh.segments = extend_to_phrases(sh.segments, phrases, float(sel["max_duration_s"]), c,
                                                    start, end)
                    if abs(sh.duration - before) > 0.05:
                        log.info("    %s: до кінця фраз %+.1f с → %.1f с", sh.title, sh.duration - before,
                                 sh.duration)
            return words
        except Exception as e:
            log.warning("[УВАГА] Розумне розпізнавання мови не спрацювало (%s: %s) — працюю як раніше. "
                        "Перевірте extra\\setup_speech_ai.bat", type(e).__name__, e)
            return None

    # ------------------------------------------------------------------ один запис
    def _process_recording(self, path: Path, idx: int, n: int) -> dict:
        st, prof, opts = self.settings, self.profile, self.opts
        mode = opts.modes.get(str(path)) or prof.get("default_mode")
        if mode:
            prof = copy.deepcopy(prof)
            prof["mode"] = mode
            log.info("Режим: %s", (prof.get("modes") or {}).get(mode, mode))
        P = self.progress
        base, span = idx / n, 1.0 / n

        def stage(name, a, b):
            P.set_stage(f"[{idx + 1}/{n}] {name}", base + span * a, span * (b - a))
            write_status(st, stage=name, percent=int(100 * (base + span * a)))

        stage("Аналіз запису", 0.0, 0.01)
        info = probe(st, path)
        log.info("Запис: %s | %d×%d, %.0f fps, %s, доріжок звуку: %d", path.name, info.width, info.height,
                 info.fps, fmt_time(info.duration, 0), info.audio_streams)
        st = resolve_recording(st, info)
        start = max(0.0, opts.start or 0.0)
        end = min(info.duration, opts.end) if opts.end else info.duration
        tag = ascii_name(path.stem)

        webcam = None
        wpath = find_webcam_file(st, path)
        if wpath:
            winfo = probe(st, wpath)
            off, how = webcam_offset(st, info, winfo)
            webcam = {"path": str(wpath), "width": winfo.width, "height": winfo.height, "fps": winfo.fps,
                      "offset": off}
            log.info("Окремий файл вебкамери: %s (%d×%d), зсув %+.3f с — %s", wpath.name, winfo.width,
                     winfo.height, off, how)
        elif st["recording"]["mode"] == "separate":
            log.info("Окремого файлу вебкамери («%s%s.mkv») немає — камера з кадру запису.",
                     path.stem, st["recording"]["separate"]["webcam_file_suffix"])

        # ---- зоровий детектор
        vision_events, battles = [], []
        if prof["signals"].get("vision"):
            stage("Розпізнавання екрану (лічильники, повідомлення про шкоду, таймер)", 0.01, 0.45)
            from .vision_detector import detect
            vision_events, battles, _ = detect(st, prof, info, start, end, progress=P.sub(0, 1))

        # ---- звук
        stage("Підготовка звуку (розділення голос/гра)", 0.45, 0.62)
        prep = AudioPrep(st, prof, info, force_mix=not opts.separation)
        chunks = prep.prepare(start, end, progress=P.sub(0, 1))
        stage("Звуки гри і ваш голос", 0.62, 0.66)
        sounds = GameSoundDetector(st, prof).detect(chunks, progress=P.sub(0, 0.5))
        exclude = [(s.t, s.t + s.extra.get("duration", 0.5)) for s in sounds if s.extra.get("stem") == "voice"]
        voices = VoiceDetector(prof).detect(chunks, exclude=exclude, progress=P.sub(0.5, 1))

        # ---- об'єднання, бої, відбір
        stage("Відбір моментів", 0.66, 0.68)
        if prof.get("mode") == "assist":
            n_ast = sum(1 for e in vision_events if e.type == E.ASSIST)
            log.info("  Режим «Засвіт»: знайдено моментів засвіту: %d", n_ast)
            if n_ast == 0 and any((b.get("assist_total") or 0) > 0 for b in battles):
                log.warning("[УВАГА] Засвіт у бою є, але жодного його зростання не відстежено — перевірте рамку "
                            "«Блок лічильників» (третій рядок) у майстрі.")
        all_events = fuse(vision_events, sounds, voices, prof)
        if prof["signals"].get("vision"):
            assign_to_battles(battles, sounds + voices)
        else:
            battles = split_by_gaps(all_events, prof["battles"]["split_gap_s"], start, end)
        speech = SpeechMap(chunks, prof["selection"]["vad_margin_db"])
        sai_cfg = dict(st.get("speech_ai", {}))
        if opts.speech_ai:
            speech = self._silero_speech(chunks, sai_cfg, speech)
        shorts = select_shorts(battles, prof, start, end, speech)
        ai_words = self._speech_ai(chunks, shorts, battles, sai_cfg, start, end, P) if opts.speech_ai and shorts else None
        log.info("Боїв: %d, шортсів: %d", len(battles), len(shorts))
        for sh in shorts:
            log.info("  %s", describe(sh))

        layout = compute_layout(prof, info.width, info.height, webcam)
        for w in layout.warnings:
            log.warning("[УВАГА] %s", w)

        # ---- субтитри
        cues_by: dict[int, list[Cue]] = {sh.index: [] for sh in shorts}
        # субтитри: галочка «Субтитри» (whisper.cpp) АБО «Повні фрази і кращі субтитри» (слова вже розпізнано)
        if shorts and (opts.subtitles or ai_words is not None):
            stage("Субтитри", 0.68, 0.80)
            try:
                if ai_words is not None:
                    log.info("  Субтитри — зі слів розумного розпізнавання (faster-whisper)")
                    words = {sh.index: words_for_short(sh, ai_words) for sh in shorts}
                else:
                    log.info("  Субтитри — whisper.cpp")
                    words = transcribe_shorts(st, shorts, chunks, self.work / tag)
                for sh in shorts:
                    cues = build_cues(sh, words.get(sh.index, []), st["subtitles"], speech,
                                      [h.lower() for h in st["whisper"]["hallucinations"]])
                    add_emoji(cues, sh, st["subtitles"])
                    cues_by[sh.index] = cues
                    log.info("    %s: %d фраз субтитрів%s", sh.title, len(cues),
                             "" if cues else " (у вибраних шматках ви майже не говорите)")
            except UserError as e:
                log.warning("[УВАГА] Субтитри не зроблено: %s", e)
        elif shorts:
            log.info("  Субтитри вимкнено (галочки «Субтитри» / «Повні фрази і кращі субтитри» не стоять)")

        # ---- файли шортсів
        shorts_out = []
        multi = len(self.opts.recordings) > 1
        from .vision_detector import battle_label
        labels = {b["index"]: battle_label(b) for b in battles}
        for sh in shorts:
            # кожен бій — окрема папка, напр. battle_01_dmg_3837 (латиницею: кнопка DaVinci не відкриває
            # шляхи з кирилицею); у назві — більша з величин «шкода» / «засвіт»
            disk_name, media_name = labels.get(sh.battle, (f"battle_{sh.battle:02d}", f"Бій {sh.battle:02d}"))
            folder = f"{tag}_{disk_name}" if multi else disk_name
            bdir = self.out / folder
            bdir.mkdir(parents=True, exist_ok=True)
            base_name = f"{tag}_battle_{sh.battle:02d}"
            srt = write_srt(cues_by[sh.index], bdir / f"{base_name}.srt") if cues_by[sh.index] else None
            if srt:
                log.info("  Субтитри: %s (і вшиті в MP4)", srt)
            cta = self._cta(sh)
            cta_srt = cta_txt = cta_png = None
            if cta:
                cta_srt = write_srt([Cue(cta["start"], cta["start"] + cta["dur"], cta["text"])], bdir / "cta.srt")
                cta_txt = bdir / "cta.txt"
                cta_txt.write_text(opts.cta_text, encoding="utf-8")
                cta_png = write_cta_png(st, cta, layout, bdir / "cta.png")
            for e in sh.events:
                e.extra["clip"] = f"{folder}/{base_name}.mp4"
            shorts_out.append({
                "index": sh.index, "battle": sh.battle, "name": sh.name, "file_base": base_name,
                "dir": folder, "media_folder": media_name,
                "duration": round(sh.duration, 3), "score": round(sh.score, 2),
                "segments": [{"s": round(s.start, 3), "e": round(s.end, 3)} for s in sh.segments],
                "layers": [l.to_dict() for l in layout.layers],
                "srt": str(srt) if srt else None,
                "cta_srt": str(cta_srt) if cta_srt else None,
                "cta_txt": str(cta_txt) if cta_txt else None,
                "cta_png": str(cta_png) if cta_png else None,
                "cta": cta,
                "mp4": str(bdir / f"{base_name}.mp4"),
                "markers": [m | {"t": round(sh.src_to_short(m["t"]) or 0, 3)}
                            for m in markers_for([e for e in sh.events if e.extra.get("moment")],
                                                 st["resolve"]["marker_colors"])],
            })

        # ---- рендер MP4
        if opts.render and st["render"]["enabled"] and shorts:
            stage("Рендер MP4", 0.80, 0.99)
            for k, (sh, so) in enumerate(zip(shorts, shorts_out)):
                P.report(k / len(shorts))
                overlays = self.overlays(cues_by[sh.index], so["cta"], layout, self.work / tag / so["file_base"])
                try:
                    render_short(st, info, layout, [(s.start, s.end) for s in sh.segments], overlays,
                                 Path(so["mp4"]), webcam)
                except UserError as e:
                    log.error("[ПОМИЛКА] %s", e)
                    so["mp4"] = None
        else:
            for so in shorts_out:
                so["mp4"] = None

        # ---- маркери, звіт
        stage("Звіт і маркери", 0.99, 1.0)
        markers = markers_for([e for e in all_events if e.type in st["resolve"]["marker_colors"]
                               and (e.type not in (E.GAME_SOUND, E.VOICE) or e.extra.get("moment")
                                    or e.extra.get("used_for_confirm") or e.extra.get("used_for_bonus"))],
                              st["resolve"]["marker_colors"])
        edl = write_marker_edl(self.out / f"{tag}_markers.edl", path.stem, markers, info.fps)
        assign_battle_events = [e for b in battles for e in b["events"]]
        known = {id(e) for e in assign_battle_events}
        report_events = assign_battle_events + [e for e in all_events if id(e) not in known]
        # окремий звіт про моменти кожного бою — у папці бою
        by_battle = {b["index"]: b for b in battles}
        for so in shorts_out:
            b = by_battle.get(so["battle"])
            if b is not None:
                write_reports(self.out / so["dir"], "moments", path, opts.profile_id, list(b["events"]), [b],
                              {"режим": prof.get("mode") or "—"})
        csv_p, json_p = write_reports(self.out, f"{tag}_report", path, opts.profile_id, report_events, battles,
                                      {"шортси": [{k: v for k, v in so.items() if k not in ("layers", "markers")}
                                                  for so in shorts_out]})
        return {
            "path": str(path), "tag": tag, "fps": info.fps, "width": info.width, "height": info.height,
            "duration": info.duration, "webcam": webcam,
            "master_name": f"{path.stem} — моменти",
            "markers": markers, "edl": str(edl), "report_csv": str(csv_p), "report_json": str(json_p),
            "layout": layout.to_dict(), "shorts": shorts_out,
            "range": [start, end],
        }

    # ------------------------------------------------------------------ заклик і картинки тексту
    def _cta(self, sh: Short) -> dict | None:
        text = (self.opts.cta_text or "").strip()
        if not text:
            return None
        dur = min(float(self.settings["cta"]["duration_s"]), sh.duration)
        return {"text": text, "start": round(max(0.0, sh.duration - dur), 3), "dur": round(dur, 3)}

    def overlays(self, cues: list[Cue], cta: dict | None, layout: Layout, tmp: Path) -> list[Overlay]:
        return build_overlays(self.settings, cues, cta, layout, tmp)


def build_overlays(settings: dict, cues: list[Cue], cta: dict | None, layout: Layout, tmp: Path) -> list[Overlay]:
    tmp.mkdir(parents=True, exist_ok=True)
    st = settings["subtitles"]
    out = []
    for i, c in enumerate(cues):
        img = render_text(c.text, st, max_width=layout.width - 80)
        p = tmp / f"cue_{i:03d}.png"
        img.save(p)
        out.append(Overlay(p, c.start, c.end, (layout.width - img.width) // 2,
                           max(0, layout.subtitles_y - img.height // 2)))
    if cta:
        img = render_text(cta["text"], st, font_size=settings["cta"]["font_size"], max_width=layout.width - 80)
        p = tmp / "cta.png"
        img.save(p)
        out.append(Overlay(p, cta["start"], cta["start"] + cta["dur"], (layout.width - img.width) // 2,
                           max(0, int(layout.cta_y or settings["cta"]["y"]) - img.height // 2)))
    return out


def write_cta_png(settings: dict, cta: dict, layout: Layout, path: Path) -> Path:
    """Заклик картинкою на весь кадр (прозоре тло) — для DaVinci, якщо титр Text+ не вставляється.
    Розмір = кадр шортсу, тож DaVinci кладе її без масштабування, текст рівно там, де в MP4."""
    from PIL import Image
    img = render_text(cta["text"], settings["subtitles"], font_size=settings["cta"]["font_size"],
                      max_width=layout.width - 80)
    frame = Image.new("RGBA", (layout.width, layout.height), (0, 0, 0, 0))
    x = (layout.width - img.width) // 2
    y = max(0, int(layout.cta_y or settings["cta"]["y"]) - img.height // 2)
    frame.alpha_composite(img, (max(0, x), y))
    frame.save(path)
    return path


def layout_from_dict(d: dict) -> Layout:
    from .layout import Layer, Rect
    layers = [Layer(l["name"], l["track"], l["source"], Rect(*l["src"]), Rect(*l["dst"]), l["src_w"], l["src_h"])
              for l in d["layers"]]
    return Layout(d["width"], d["height"], d["fps"], d["preset"], layers, d["subtitles_y"], d.get("warnings", []),
                  int(d.get("cta_y") or 0))
