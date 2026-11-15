"""Перехідник: модуль перенесено в autocut/core/util.py (старий імпорт shorts.util працює далі)."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("autocut.core.util")
