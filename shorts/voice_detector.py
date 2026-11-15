"""Перехідник: модуль перенесено в autocut/core/voice_detector.py (старий імпорт shorts.voice_detector працює далі)."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("autocut.core.voice_detector")
