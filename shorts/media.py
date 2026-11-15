"""Перехідник: модуль перенесено в autocut/core/media.py (старий імпорт shorts.media працює далі)."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("autocut.core.media")
