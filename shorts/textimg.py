"""Перехідник: модуль перенесено в autocut/core/textimg.py (старий імпорт shorts.textimg працює далі)."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("autocut.core.textimg")
