"""Перехідник: модуль перенесено в autocut/core/recording.py (старий імпорт shorts.recording працює далі)."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("autocut.core.recording")
