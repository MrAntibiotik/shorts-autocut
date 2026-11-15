"""Перехідник: модуль перенесено в autocut/core/render.py (старий імпорт shorts.render працює далі)."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("autocut.core.render")
