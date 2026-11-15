"""Перехідник: модуль перенесено в autocut/core/audio.py (старий імпорт shorts.audio працює далі)."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("autocut.core.audio")
