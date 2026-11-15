"""Перехідник: модуль перенесено в autocut/core/logs.py (старий імпорт shorts.logs працює далі)."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("autocut.core.logs")
