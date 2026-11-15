"""Перехідник: модуль перенесено в autocut/core/layout.py (старий імпорт shorts.layout працює далі)."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("autocut.core.layout")
