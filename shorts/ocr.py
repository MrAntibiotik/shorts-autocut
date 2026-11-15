"""Перехідник: модуль перенесено в autocut/core/ocr.py (старий імпорт shorts.ocr працює далі)."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("autocut.core.ocr")
