"""Перехідник: модуль перенесено в autocut/core/errors.py (старий імпорт shorts.errors працює далі)."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("autocut.core.errors")
