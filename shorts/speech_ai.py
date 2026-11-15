"""Перехідник: модуль перенесено в autocut/core/speech_ai.py (старий імпорт shorts.speech_ai працює далі)."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("autocut.core.speech_ai")
