"""Перехідник: модуль перенесено в autocut/core/audio_detector.py (старий імпорт shorts.audio_detector працює далі)."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("autocut.core.audio_detector")
