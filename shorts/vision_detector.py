"""Перехідник: модуль перенесено в autocut/core/vision_detector.py (старий імпорт shorts.vision_detector працює далі)."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("autocut.core.vision_detector")
