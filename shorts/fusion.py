"""Перехідник: модуль перенесено в autocut/core/fusion.py (старий імпорт shorts.fusion працює далі)."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("autocut.core.fusion")
