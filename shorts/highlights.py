"""Перехідник: модуль перенесено в autocut/main/highlights.py (старий імпорт shorts.highlights працює далі)."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("autocut.main.highlights")
