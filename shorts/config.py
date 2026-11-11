"""Завантаження налаштувань (config/settings.json) і профілів ігор (config/profiles/*.json).

Усі параметри живуть у JSON-файлах. Якщо якогось ключа у файлі немає —
береться значення за замовчуванням звідси, тож старі профілі не ламаються,
коли в нових версіях з'являються нові параметри.
Ключі, що починаються з «_» (наприклад «_опис»), — це коментарі, код їх ігнорує.
"""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

from .errors import UserError

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
PROFILES_DIR = CONFIG_DIR / "profiles"
DEFAULTS_DIR = ROOT / "defaults" / "profiles"     # стандартні профілі (оновлює SETUP.bat)
SETTINGS_FILE = CONFIG_DIR / "settings.json"

# ------------------------------------------------------------------ типові значення

DEFAULT_SETTINGS: dict = {
    "paths": {
        "ffmpeg": "auto",
        "ffprobe": "auto",
        "work_dir": "work",
        "output_dir": "output",
        "models_dir": "models",
    },
    "recording": {
        "mode": "mixed",
        "mixed": {"audio_track": 0},
        "auto": {"enabled": True, "min_tracks": 3, "mic_audio_track": 1, "game_audio_track": 2},
        "separate": {
            "game_audio_track": 0,
            "mic_audio_track": 1,
            "webcam_file_suffix": "_webcam",
            "webcam_search_dirs": [],
        },
    },
    "processing": {"chunk_minutes": 10, "hwaccel": "cuda"},
    "separation": {"enabled": True, "model": "htdemucs", "device": "cuda", "shifts": 0, "overlap": 0.25},
    "cleanup": {
        "enabled": True,
        "keep_days": 14,
    },
    "render": {
        "enabled": True,
        "encoder": "h264_nvenc",
        "nvenc_preset": "p5",
        "cq": 19,
        "max_bitrate": "20M",
        "audio_bitrate": "192k",
        "fallback_encoder": "libx264",
        "x264_crf": 18,
    },
    "whisper": {
        "enabled": False,
        "cli": "auto",
        "model": "models/whisper/ggml-large-v3-turbo.bin",
        "language": "uk",
        "gpu": True,
        "threads": 6,
        "beam_size": 5,
        "prompt": "Стрім українською. World of Tanks, PUBG, Сталкер.",
        "hallucinations": ["дякую за перегляд", "субтитри", "підписуйтесь на канал", "продовження слідує",
                           "дякуємо за увагу", "редактор субтитрів", "субтитрувальниця"],
    },
    "speech_ai": {
        "enabled": False,
        "model": "large-v3",
        "models_dir": "models/faster-whisper",
        "device": "cuda",
        "compute_type": "float16",
        "cpu_threads": 6,
        "language": "uk",
        "beam_size": 5,
        "prompt": "Стрім українською. World of Tanks, PUBG, Сталкер.",
        "min_word_prob": 0.15,
        "no_speech_threshold": 0.6,
        "use_silero_vad": True,
        "vad_threshold": 0.5,
        "vad_min_speech_ms": 200,
        "vad_min_silence_ms": 250,
        "vad_pad_ms": 60,
        "window_before_s": 15,
        "window_after_s": 25,
        "phrase_gap_s": 0.7,
        "min_phrase_prob": 0.5,
        "filter_game_phrases": True,
        "game_phrases": [],
        "game_words": [],
        "sentence_cut": True,
        "sentence_max_before_s": 2.0,
        "sentence_max_after_s": 5.0,
        "hallucinations": ["дякую за перегляд", "субтитри", "підписуйтесь на канал", "продовження слідує", "дякуємо за увагу", "редактор субтитрів", "субтитрувальниця"],
    },
    "subtitles": {
        "font": "C:/Windows/Fonts/arialbd.ttf",
        "emoji_font": "C:/Windows/Fonts/seguiemj.ttf",
        "font_size": 66,
        "color": "#FFFFFF",
        "outline_color": "#000000",
        "outline_width": 6,
        "shadow_offset": 5,
        "shadow_alpha": 170,
        "max_chars_per_line": 24,
        "max_lines": 2,
        "max_cue_s": 2.8,
        "min_cue_s": 0.6,
        "pause_split_s": 0.45,
        "emotion_emoji": {"крик": "😱", "сміх": "😂"},
        "keyword_emoji": {"знищ": "💥", "пробит": "🎯", "ваншот": "🔥"},
    },
    "cta": {
        "duration_s": 3.0,
        "font_size": 70,
        "y": 1680,
    },
    "resolve": {
        "mismatch_mode": "auto",
        "crop_units": "fitted",
        "position_units": "fitted",
        "tilt_up_positive": True,
        "add_short_markers": True,
        "link_clips": True,
        "project_mode": "new",
        "marker_colors": {
            "hit": "Green", "damage_taken": "Red", "kill": "Yellow", "death": "Fuchsia",
            "voice": "Cyan", "game_sound": "Blue", "battle_start": "Cream", "battle_end": "Cream",
        },
    },
    "ocr": {
        "languages": ["uk", "en"],
        "gpu": True,
        "digit_upscale": 2.0,
        "text_upscale": 1.5,
        "min_confidence": 0.25,
        "change_pixel_delta": 40,
        "change_fraction": 0.008,
        "refresh_s": 1.0,
    },
}

DEFAULT_PROFILE: dict = {
    "name": "Гра",
    "zones_confirmed": False,
    "zones": {},
    "signals": {"vision": False, "game_audio": True, "voice": True},
    "vision": {
        "sample_fps": 2,
        "time_correction_s": -0.25,
        "damage_counter": {
            "enabled": True,
            "max_jump": 3000,
            "max_value": 30000,
            "confirm_frames": 2,
            "reset_confirm_frames": 4,
            "resync_frames": 6,
        },
        "other_counters": {
            "enabled": False,
            "blocked_from_block": [0.28, 0.32, 0.72, 0.36],
            "assist_from_block": [0.28, 0.64, 0.72, 0.36],
        },
        "hit_message": {
            "enabled": False,
            "keywords": ["шкода"],
            "keyword_ratio": 0.7,
            "min_value": 1,
            "max_value": 5000,
            "confirm_frames": 2,
            "require_keyword": False,
            "reject_colors": ["red"],
            "reject_keywords": ["розвід", "стриман", "виявив", "інформа", "помістит", "відновлю", "знайомст", "заблок", "ремонт", "запас"],
            "max_prefix_letters": 3,
        },
        "battle_timer": {
            "enabled": False,
            "check_every_s": 1.0,
            "max_base_s": 1200,
            "default_base_s": 900,
            "min_base_s": 300,
            "freeze_s": 5.0,
            "bases_s": [600, 900, 1200],
            "start_window_s": 60,
            "end_gap_s": 30,
            "resync_readings": 10,
        },
        "event_log": {
            "enabled": True,
            "kill_keywords": ["знищив", "знищено"],
            "keyword_ratio": 0.75,
            "memory_s": 20,
            "match_ratio": 0.75,
            "min_chars": 3,
            "confirm_frames": 1,
        },
        "hp": {
            "enabled": True,
            "use_text": True,
            "use_bar": True,
            "min_drop": 30,
            "bar_min_drop": 0.04,
            "confirm_frames": 2,
        },
        "end_screen": {
            "enabled": True,
            "check_every_s": 2.0,
            "scale": 0.6,
            "death_keywords": ["оцініть бій", "перемкнути камеру"],
            "battle_end_keywords": ["перемога", "поразка", "нічия"],
            "keyword_ratio": 0.8,
            "cooldown_s": 60,
        },
    },
    "battles": {"split_gap_s": 45, "merge_death_s": 20, "log_match_s": 2.0},
    "selection": {
        "max_duration_s": 60,
        "pre_s": [2.0, 3.0],
        "post_s": [2.0, 3.0],
        "death_pre_s": 4.0,
        "death_post_s": 1.5,
        "merge_gap_s": 4.0,
        "min_score": 1.0,
        "min_battle_score": 2.0,
        "min_short_s": 5.0,
        "always_include_final": True,
        "speech_snap": True,
        "snap_max_s": 1.5,
        "snap_after_max_s": 1.5,
        "min_pause_s": 0.25,
        "compact_pre_s": 1.5,
        "compact_post_s": 3.5,
        "confirmed_first": True,
        "vad_margin_db": 10,
        "max_shorts": 0,
    },
    "game_audio": {
        "categories": {},
        "stems": ["game", "voice", "mix"],
        "threshold": 0.6,
        "mix_threshold_factor": 0.85,
        "min_gap_s": 0.6,
        "level_tolerance_db": 20,
        "loudness_spikes": {
            "enabled": False,
            "spike_db": 12,
            "mix_extra_db": 3,
            "min_duration_s": 0.1,
            "baseline_window_s": 30,
            "floor_db": -55,
            "weight": 0.4,
        },
    },
    "voice": {
        "spike_db": 10,
        "mix_spike_db": 14,
        "min_duration_s": 0.15,
        "scream_min_db": 12,
        "scream_min_hz": 300,
        "scream_min_duration_s": 0.3,
        "baseline_window_s": 60,
        "floor_db": -50,
        "merge_s": 1.5,
        "laugh": {"enabled": True, "window_s": 1.5, "min_db": 3, "min_periodicity": 0.4, "min_bursts": 3,
                  "max_irregularity": 0.35, "min_hz": 170},
    },
    "scoring": {
        "base": {"hit": 1.0, "kill": 3.0, "death": 2.5, "damage_taken": 0.5, "game_sound": 1.0, "voice": 0.8},
        "small_hit_damage": 100,
        "small_hit_base": 0.3,
        "hit_damage_scale": 500,
        "hit_damage_cap": 3.0,
        "taken_damage_scale": 2000,
        "sound_confirm_window_s": 1.0,
        "sound_confirm_mult": 1.3,
        "emotion_window_s": [0, 3],
        "emotion_bonus": 1.0,
        "audio_only_moments": False,
        "voice_only_moments": False,
        "min_score_audio_only": 0.8,
    },
    "layout": {
        "preset": "split",
        "output": {"width": 1080, "height": 1920, "fps": 60},
        "split": {"webcam_height": 550, "game_zoom": 1.0, "game_offset_x": 0.0, "game_offset_y": 0.0,
                  "webcam_focus_y": 0.5, "webcam_fit": "cover"},
        "overlay": {"webcam_x": 220, "webcam_y": 40, "webcam_width": 640, "webcam_height": 410,
                    "game_zoom": 1.0, "game_offset_x": 0.0, "game_offset_y": 0.0, "webcam_focus_y": 0.5,
                    "webcam_fit": "cover", "game_below_webcam": False},
        "counters": {"enabled": False, "anchor": "absolute", "align": "center", "gap": 12,
                     "x": 0, "y": 1250, "width": 225, "height": 0},
        "subtitles_y": 1450,
        "avoid_webcam_in_game": True,
    },
}

# Зони, які використовує зоровий детектор (решта — для макета).
VISION_ZONES = ["damage_counter", "blocked_counter", "assist_counter", "hit_message", "battle_timer",
                "event_log", "hp_text", "hp_bar", "end_screen"]


# ------------------------------------------------------------------ утиліти

def deep_merge(base: dict, override: dict) -> dict:
    """Рекурсивно накладає override на копію base."""
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def strip_comments(d):
    if isinstance(d, dict):
        return {k: strip_comments(v) for k, v in d.items() if not str(k).startswith("_")}
    if isinstance(d, list):
        return [strip_comments(x) for x in d]
    return d


def _read_json(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except FileNotFoundError:
        raise UserError(f"Не знайдено файл {path}",
                        "Перевірте, що папку інструмента скопійовано повністю.")
    except json.JSONDecodeError as e:
        raise UserError(
            f"Помилка у файлі {path.name}, рядок {e.lineno}, позиція {e.colno}: {e.msg}",
            "Найчастіше це зайва або пропущена кома чи лапки. Відкрийте файл у Блокноті "
            "і виправте цей рядок (або поверніть копію .bak).")


def resolve_path(p: str | Path) -> Path:
    """Відносні шляхи рахуються від папки інструмента."""
    p = Path(p)
    return p if p.is_absolute() else (ROOT / p)


# ------------------------------------------------------------------ налаштування

def load_settings() -> dict:
    user = _read_json(SETTINGS_FILE) if SETTINGS_FILE.exists() else {}
    return deep_merge(DEFAULT_SETTINGS, strip_comments(user))


def work_dir(settings: dict) -> Path:
    d = resolve_path(settings["paths"]["work_dir"])
    d.mkdir(parents=True, exist_ok=True)
    return d


def output_dir(settings: dict) -> Path:
    d = resolve_path(settings["paths"]["output_dir"])
    d.mkdir(parents=True, exist_ok=True)
    return d


def models_dir(settings: dict) -> Path:
    d = resolve_path(settings["paths"]["models_dir"])
    d.mkdir(parents=True, exist_ok=True)
    return d


# ------------------------------------------------------------------ профілі

def list_profiles() -> list[str]:
    return sorted(p.stem for p in PROFILES_DIR.glob("*.json"))


def profile_path(name: str) -> Path:
    return PROFILES_DIR / f"{name}.json"


def load_profile_raw(name: str) -> dict:
    path = profile_path(name)
    if not path.exists():
        raise UserError(f"Профіль гри «{name}» не знайдено ({path}).",
                        f"Доступні профілі: {', '.join(list_profiles()) or 'немає'}.")
    return _read_json(path)


def load_profile(name: str) -> dict:
    prof = deep_merge(DEFAULT_PROFILE, strip_comments(load_profile_raw(name)))
    prof["id"] = name
    validate_zones(prof)
    return prof


def reset_profile(name: str, keep_zones: bool = True) -> Path:
    """Повертає профіль гри до стандартного. keep_zones — залишити ваші обведені рамки.
    Поточна версія зберігається поруч як .json.bak."""
    src = DEFAULTS_DIR / f"{name}.json"
    if not src.exists():
        raise UserError(f"Не знайдено стандартний профіль «{name}» ({src}).",
                        "Запустіть SETUP.bat ще раз — він покладе стандартні профілі в папку defaults.")
    new = _read_json(src)
    if keep_zones and profile_path(name).exists():
        try:
            old = _read_json(profile_path(name))
            for key in ("zones", "zones_confirmed"):
                if key in old:
                    new[key] = old[key]
        except UserError:
            pass   # пошкоджений профіль — просто беремо стандартний
    return save_profile_raw(name, new)


def save_profile_raw(name: str, data: dict) -> Path:
    """Зберігає профіль, попередню версію кладе поруч як .bak."""
    path = profile_path(name)
    if path.exists():
        shutil.copy2(path, path.with_suffix(".json.bak"))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path


def validate_zones(prof: dict) -> None:
    for key, z in (prof.get("zones") or {}).items():
        ok = (isinstance(z, list) and len(z) == 4
              and all(isinstance(v, (int, float)) for v in z)
              and 0 <= z[0] < 1 and 0 <= z[1] < 1 and 0 < z[2] <= 1 and 0 < z[3] <= 1
              and z[0] + z[2] <= 1.0001 and z[1] + z[3] <= 1.0001)
        if not ok:
            raise UserError(
                f"Зона «{key}» у профілі «{prof.get('id')}» задана неправильно: {z}",
                "Формат: [x, y, ширина, висота] у частках кадру від 0 до 1. "
                "Найпростіше — заново обвести зону через extra\\pick_zones.bat.")


def zone_px(zone: list[float], width: int, height: int) -> tuple[int, int, int, int]:
    """Частки кадру -> пікселі (x, y, w, h), обрізано по межах кадру, мінімум 2×2."""
    x = int(round(zone[0] * width))
    y = int(round(zone[1] * height))
    w = int(round(zone[2] * width))
    h = int(round(zone[3] * height))
    x = max(0, min(x, width - 2))
    y = max(0, min(y, height - 2))
    w = max(2, min(w, width - x))
    h = max(2, min(h, height - y))
    return x, y, w, h


def ask_profile(default: str | None = None) -> str:
    """Питає в консолі, який профіль гри використати."""
    names = list_profiles()
    if not names:
        raise UserError("У папці config/profiles немає жодного профілю гри.",
                        "Скопіюйте папку інструмента повністю.")
    print("\nДоступні профілі ігор:")
    for i, n in enumerate(names, 1):
        try:
            title = strip_comments(load_profile_raw(n)).get("name", n)
        except UserError:
            title = n
        print(f"  {i}. {title}  ({n})")
    hint = f" [Enter = {default}]" if default in names else ""
    while True:
        ans = input(f"Номер профілю{hint}: ").strip()
        if not ans and default in names:
            return default
        if ans.isdigit() and 1 <= int(ans) <= len(names):
            return names[int(ans) - 1]
        if ans in names:
            return ans
        print("  Не зрозумів. Введіть номер зі списку.")
