"""Перехідник: модуль перенесено в autocut/core/subtitles.py (старий імпорт shorts.subtitles працює далі)."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("autocut.core.subtitles")
