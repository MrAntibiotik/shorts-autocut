"""Перехідник: модуль перенесено в autocut/core/config.py (старий імпорт shorts.config працює далі)."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("autocut.core.config")
