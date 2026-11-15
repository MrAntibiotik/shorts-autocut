"""Перехідник: модуль перенесено в autocut/core/report.py (старий імпорт shorts.report працює далі)."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("autocut.core.report")
