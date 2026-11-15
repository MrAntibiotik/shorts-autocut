"""Перехідник: модуль перенесено в autocut/core/events.py (старий імпорт shorts.events працює далі)."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("autocut.core.events")
