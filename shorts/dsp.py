"""Перехідник: модуль перенесено в autocut/core/dsp.py (старий імпорт shorts.dsp працює далі)."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("autocut.core.dsp")
