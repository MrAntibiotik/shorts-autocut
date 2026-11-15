"""Перехідник: модуль перенесено в autocut/core/selection.py (старий імпорт shorts.selection працює далі)."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("autocut.core.selection")
