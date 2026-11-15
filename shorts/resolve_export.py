"""Перехідник: модуль перенесено в autocut/core/resolve_export.py (старий імпорт shorts.resolve_export працює далі)."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("autocut.core.resolve_export")
