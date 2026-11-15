"""Перехідник: модуль перенесено в autocut/core/cleanup.py (старий імпорт shorts.cleanup працює далі)."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("autocut.core.cleanup")
