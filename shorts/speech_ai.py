"""ЕКСПЕРИМЕНТАЛЬНО: «розумне» розпізнавання мови. За замовчуванням ВИМКНЕНО.

Що дає (коли увімкнено галочкою у вікні або settings.json → speech_ai.enabled):
  * Silero VAD — нейромережа, що відрізняє саме мову від шуму/гри/дихання. Межі фраз точніші,
    ніж за гучністю (старий спосіб лишається запасним).
  * faster-whisper — текст і час КОЖНОГО слова. Розпізнається не склеєний шматок шортсу,
    а ширше вікно навколо моментів бою (з контекстом), результат кешується.
  * Нарізка за реченнями: кінець шматка продовжується до кінця вашої фрази, початок —
    до її початку (в межах ліміту тривалості шортсу).
  * Субтитри з цих слів (замість whisper.cpp), якщо субтитри увімкнено.

Будь-яка помилка тут НЕ зупиняє обробку: пишеться попередження і працює старий спосіб.
Встановлення: extra\\setup_speech_ai.bat. Перевірка на відрізку запису: extra\\test_speech.bat.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .audio import SR_VOICE, AudioChunk
from .config import resolve_path

log = logging.getLogger("speech_ai")

VAD_STEP = 0.02           # як у SpeechMap: 50 значень на секунду
_END_PUNCT = re.compile(r"[.!?…]+[\"»”)]*$")


@dataclass
class AiWord:
    text: str
    start: float          # час у записі
    end: float
    prob: float = 1.0


@dataclass
class Phrase:
    start: float
    end: float
    text: str
    words: list[AiWord]


def cfg_of(settings: dict) -> dict:
    return settings.get("speech_ai", {}) or {}


def is_enabled(settings: dict) -> bool:
    return bool(cfg_of(settings).get("enabled", False))


# ====================================================================== аудіо

def voice_audio(chunks: list[AudioChunk], a: float, b: float) -> np.ndarray:
    """Доріжка голосу (16 кГц, моно) на відрізку [a, b] запису."""
    parts = []
    for ch in chunks:
        if ch.c1 <= a or ch.c0 >= b:
            continue
        y, sr = ch.load("voice")
        if y.ndim > 1:
            y = y.mean(axis=1)
        i0 = max(0, int((a - ch.c0) * sr))
        i1 = min(len(y), int((b - ch.c0) * sr))
        if i1 > i0:
            parts.append(y[i0:i1].astype(np.float32))
    return np.concatenate(parts) if parts else np.zeros(0, np.float32)


# ====================================================================== Silero VAD

_VAD_MODEL = None


def _vad_model():
    global _VAD_MODEL
    if _VAD_MODEL is None:
        from silero_vad import load_silero_vad
        _VAD_MODEL = load_silero_vad()
    return _VAD_MODEL


def silero_mask(y: np.ndarray, cfg: dict) -> np.ndarray:
    """Маска «тут мова» з кроком VAD_STEP для звуку 16 кГц."""
    import torch
    from silero_vad import get_speech_timestamps
    n = int(len(y) / SR_VOICE / VAD_STEP) + 1
    mask = np.zeros(n, bool)
    if len(y) < SR_VOICE // 2:
        return mask
    stamps = get_speech_timestamps(
        torch.from_numpy(np.ascontiguousarray(y, dtype=np.float32)), _vad_model(),
        sampling_rate=SR_VOICE,
        threshold=float(cfg.get("vad_threshold", 0.5)),
        min_speech_duration_ms=int(cfg.get("vad_min_speech_ms", 200)),
        min_silence_duration_ms=int(cfg.get("vad_min_silence_ms", 250)),
        speech_pad_ms=int(cfg.get("vad_pad_ms", 60)),
    )
    for s in stamps:
        i0 = int(s["start"] / SR_VOICE / VAD_STEP)
        i1 = int(np.ceil(s["end"] / SR_VOICE / VAD_STEP))
        mask[i0:i1] = True
    return mask


def silero_parts(chunks: list[AudioChunk], cfg: dict) -> list[tuple[float, np.ndarray]]:
    """[(початок частини, маска)] — у форматі SpeechMap.parts."""
    out = []
    for ch in chunks:
        y, sr = ch.load("voice")
        if y.ndim > 1:
            y = y.mean(axis=1)
        if sr != SR_VOICE:
            import librosa
            y = librosa.resample(y, orig_sr=sr, target_sr=SR_VOICE)
        out.append((ch.c0, silero_mask(y, cfg)))
    return out


# ====================================================================== faster-whisper

_WHISPER = None
_WHISPER_KEY = None


def _prepare_cuda_dlls() -> None:
    """Windows: faster-whisper (CTranslate2) шукає cuBLAS/cuDNN. Вони вже є в PyTorch (torch\\lib) —
    підключаємо їх, щоб не ставити CUDA окремо."""
    if os.name != "nt":
        return
    try:
        import torch
        lib = Path(torch.__file__).parent / "lib"
        if lib.is_dir():
            os.add_dll_directory(str(lib))
            os.environ["PATH"] = str(lib) + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass


def model_dir(cfg: dict) -> Path:
    return resolve_path(cfg.get("models_dir", "models/faster-whisper"))


def model_path(cfg: dict) -> str:
    """Локальна папка моделі, якщо завантажена (extra\\setup_speech_ai.bat), інакше назва для завантаження."""
    name = cfg.get("model", "large-v3")
    local = model_dir(cfg) / name
    return str(local) if (local / "model.bin").exists() else name


def load_whisper(cfg: dict):
    global _WHISPER, _WHISPER_KEY
    key = (model_path(cfg), cfg.get("device", "cuda"), cfg.get("compute_type", "float16"))
    if _WHISPER is not None and _WHISPER_KEY == key:
        return _WHISPER
    _prepare_cuda_dlls()
    from faster_whisper import WhisperModel
    log.info("  Завантажую модель розпізнавання мови (%s, %s)…", cfg.get("model", "large-v3"), key[1])
    _WHISPER = WhisperModel(key[0], device=key[1], compute_type=key[2],
                            download_root=str(model_dir(cfg)), cpu_threads=int(cfg.get("cpu_threads", 6)))
    _WHISPER_KEY = key
    return _WHISPER


def transcribe_audio(y: np.ndarray, cfg: dict, offset: float = 0.0) -> list[AiWord]:
    """Розпізнає звук 16 кГц. Час слів — у записі (offset + час у шматку)."""
    if len(y) < SR_VOICE // 2:
        return []
    peak = float(np.abs(y).max())
    if 1e-4 < peak < 0.5:                      # тихий голос — підсилюємо до нормального рівня
        y = y * (0.7 / peak)
    model = load_whisper(cfg)
    segments, _ = model.transcribe(
        y.astype(np.float32),
        language=cfg.get("language", "uk"),
        beam_size=int(cfg.get("beam_size", 5)),
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": int(cfg.get("vad_min_silence_ms", 250))},
        initial_prompt=cfg.get("prompt") or None,
        condition_on_previous_text=False,       # менше «зациклених» вигадок
        no_speech_threshold=float(cfg.get("no_speech_threshold", 0.6)),
    )
    words: list[AiWord] = []
    for seg in segments:
        for w in (seg.words or []):
            t = (w.word or "").strip()
            if t:
                words.append(AiWord(t, offset + float(w.start), offset + float(w.end), float(w.probability)))
    return _clean(words, cfg)


def _clean(words: list[AiWord], cfg: dict) -> list[AiWord]:
    """Прибирає «галюцинації» whisper: службові фрази, слова з дуже низькою впевненістю."""
    min_p = float(cfg.get("min_word_prob", 0.15))
    words = [w for w in words if w.prob >= min_p and not w.text.startswith(("[", "("))]
    text = " ".join(w.text for w in words).lower()
    for h in cfg.get("hallucinations", []):
        if h in text and len(text) < len(h) + 25:
            return []
    return words


# ====================================================================== фрази

def phrases_of(words: list[AiWord], max_gap_s: float = 0.7) -> list[Phrase]:
    """Слова -> фрази: межа — кінець речення (. ! ? …) або пауза ≥ max_gap_s."""
    out: list[Phrase] = []
    cur: list[AiWord] = []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        if nxt is None or _END_PUNCT.search(w.text) or nxt.start - w.end >= max_gap_s:
            out.append(Phrase(cur[0].start, cur[-1].end, " ".join(x.text for x in cur), cur))
            cur = []
    return out


# ====================================================================== фільтр фраз (гра, вигадки моделі)

GAME_PHRASES = [
    # голос екіпажу / швидкі команди WoT (українська озвучка) — можна доповнювати в settings.json
    "потрібна допомога", "чекаю наказу відкрити вогонь", "відкрити вогонь", "ворога виявлено",
    "нас виявили", "нас помітили", "є пробиття", "не пробив", "рикошет", "ворога знищено",
    "танк знищено", "ціль знищено", "двигун пошкоджено", "гармату пошкоджено", "гусеницю збито",
    "боєукладку пошкоджено", "пожежа", "загоряння", "командира контужено", "навідника контужено",
    "механіка-водія контужено", "радиста контужено", "заряджаючого контужено", "вогнегасник",
    "атакую", "тримаю позицію", "захоплюємо базу", "захищаємо базу", "так точно", "ніяк ні",
    "дякую", "відступаємо", "за мною",
]
GAME_WORDS = ["пошкоджено", "контужено", "збито", "знищено", "наказу"]


def _norm(t: str) -> list[str]:
    return [w for w in re.sub(r"[^\w\s'ʼ-]", " ", t.lower()).replace("-", " ").split() if w]


def _similar(a: str, b: str, r: float = 0.75) -> bool:
    if a == b:
        return True
    from difflib import SequenceMatcher
    return min(len(a), len(b)) >= 3 and SequenceMatcher(None, a, b).ratio() >= r


def _covers(phrase_toks: list[str], pattern: str, need: float) -> bool:
    """Частка слів шаблону, що (приблизно) є у фразі."""
    pt = _norm(pattern)
    if not pt:
        return False
    hit = sum(1 for p in pt if any(_similar(p, w) for w in phrase_toks))
    return hit / len(pt) >= need


def phrase_reject_reason(p: Phrase, cfg: dict) -> str | None:
    toks = _norm(p.text)
    if not toks:
        return "порожньо"
    ws = p.words
    zero = sum(1 for w in ws if w.end - w.start < 0.03)
    if len(ws) >= 3 and zero / len(ws) >= 0.5:
        return "вигадка моделі (слова без тривалості)"
    mean_p = sum(w.prob for w in ws) / len(ws)
    if mean_p < float(cfg.get("min_phrase_prob", 0.5)):
        return f"низька впевненість ({mean_p:.2f})"
    for h in cfg.get("hallucinations", []):
        if len(_norm(h)) >= 2 and _covers(toks, h, 0.67):
            return f"типова вигадка моделі («{h}»)"
    if cfg.get("filter_game_phrases", True):
        phrases = list(cfg.get("game_phrases") or GAME_PHRASES)
        for g in phrases:
            gt = _norm(g)
            if len(gt) >= 2 and _covers(toks, g, 0.7) and len(toks) <= len(gt) + 3:
                return f"фраза з гри («{g}»)"
            # обірвана фраза гри: 1–2 слова, усі — з фрази гри («Потрібна» … «допомога!»)
            if len(toks) <= 2 and all(any(_similar(t, x) for x in gt) for t in toks) and \
                    any(len(t) >= 5 for t in toks):
                return f"шматок фрази з гри («{g}»)"
        if len(toks) <= 4:
            for gw in cfg.get("game_words") or GAME_WORDS:
                if any(_similar(gw, t, 0.8) for t in toks):
                    return f"фраза з гри (слово «{gw}»)"
    return None


def filter_phrases(words: list[AiWord], cfg: dict) -> tuple[list[AiWord], list[tuple[Phrase, str]]]:
    """Прибирає фрази з гри (голос екіпажу) і вигадки моделі. Повертає (слова, [(фраза, причина)])."""
    kept: list[AiWord] = []
    dropped: list[tuple[Phrase, str]] = []
    for p in phrases_of(words, float(cfg.get("phrase_gap_s", 0.7))):
        why = phrase_reject_reason(p, cfg)
        if why:
            dropped.append((p, why))
        else:
            kept += p.words
    return kept, dropped


def windows_for(battles: list[dict], cfg: dict, rec_start: float, rec_end: float) -> list[tuple[float, float]]:
    """Вікна для розпізнавання: навколо кожного моменту бою (з контекстом), близькі — зливаються."""
    before, after = float(cfg.get("window_before_s", 15)), float(cfg.get("window_after_s", 25))
    ws = []
    for b in battles:
        for e in b["events"]:
            if e.extra.get("moment"):
                ws.append((max(rec_start, e.t - before), min(rec_end, e.t + after)))
    ws.sort()
    out: list[list[float]] = []
    for a, b in ws:
        if out and a <= out[-1][1] + 5:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


class SpeechAI:
    """Розпізнавання вікон із кешем на диску (повторний запуск — миттєво)."""

    def __init__(self, settings: dict, cache_dir: Path):
        self.settings = settings
        self.cfg = cfg_of(settings)
        key = json.dumps({k: self.cfg.get(k) for k in ("model", "language", "beam_size", "prompt",
                                                       "min_word_prob", "no_speech_threshold")},
                         sort_keys=True, ensure_ascii=False)
        self.dir = cache_dir / f"speech_{hashlib.md5(key.encode()).hexdigest()[:8]}"
        self.dir.mkdir(parents=True, exist_ok=True)
        from .cleanup import drop_stale_siblings
        drop_stale_siblings(settings, self.dir)

    def words(self, chunks: list[AudioChunk], windows: list[tuple[float, float]], progress=None) -> list[AiWord]:
        out: list[AiWord] = []
        for i, (a, b) in enumerate(windows):
            f = self.dir / f"win_{a:09.2f}_{b:09.2f}.json"
            if f.exists():
                ws = [AiWord(**d) for d in json.loads(f.read_text(encoding="utf-8"))]
            else:
                ws = transcribe_audio(voice_audio(chunks, a, b), self.cfg, offset=a)
                f.write_text(json.dumps([w.__dict__ for w in ws], ensure_ascii=False), encoding="utf-8")
            out += ws
            if progress:
                progress((i + 1) / max(1, len(windows)))
        out.sort(key=lambda w: w.start)
        # слова з вікон, що перекриваються, — без дублів
        dedup: list[AiWord] = []
        for w in out:
            if dedup and abs(w.start - dedup[-1].start) < 0.05 and w.text == dedup[-1].text:
                continue
            dedup.append(w)
        kept, dropped = filter_phrases(dedup, self.cfg)
        for p, why in dropped:
            log.info("    відкинуто фразу «%s» — %s", p.text[:60], why)
        return kept


# ====================================================================== нарізка за реченнями

def extend_to_phrases(segs: list, phrases: list[Phrase], limit: float, cfg: dict,
                      rec_start: float, rec_end: float) -> list:
    """Якщо шматок шортсу обривається посеред вашої фрази — продовжує його до кінця фрази
    (не більше ніж на sentence_max_after_s), а початок — до початку фрази (до sentence_max_before_s).
    Загальна тривалість не перевищує limit. segs — список selection.Segment."""
    from .selection import Segment, _merge, _total
    max_after = float(cfg.get("sentence_max_after_s", 5.0))
    max_before = float(cfg.get("sentence_max_before_s", 2.0))
    gap = float(cfg.get("merge_gap_s", 4.0))
    out = [Segment(s.start, s.end, list(s.events)) for s in segs]
    for s in out:
        for p in phrases:
            if p.start < s.end < p.end and p.end - s.end <= max_after:       # обрізано посеред фрази
                extra = p.end + 0.25 - s.end
                if _total(out) + extra <= limit:
                    s.end = min(rec_end, p.end + 0.25)
            if p.start < s.start < p.end and s.start - p.start <= max_before:
                extra = s.start - (p.start - 0.15)
                if _total(out) + extra <= limit:
                    s.start = max(rec_start, p.start - 0.15)
    return _merge(out, gap)


def words_for_short(sh, words: list[AiWord]) -> list:
    """Слова, що потрапили в шматки шортсу, — у форматі subtitles.Word (час у записі)."""
    from .subtitles import Word
    res = []
    for w in words:
        mid = (w.start + w.end) / 2
        if any(s.start <= mid <= s.end for s in sh.segments):
            res.append(Word(w.text, w.start, w.end))
    return res


def available() -> tuple[bool, str]:
    """Чи встановлено бібліотеки (без завантаження моделей)."""
    try:
        import faster_whisper  # noqa: F401
        import silero_vad  # noqa: F401
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


__all__ = ["AiWord", "Phrase", "SpeechAI", "available", "cfg_of", "extend_to_phrases", "is_enabled",
           "phrases_of", "silero_parts", "transcribe_audio", "voice_audio", "windows_for", "words_for_short",
           ]
