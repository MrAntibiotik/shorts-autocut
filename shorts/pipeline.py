"""Перехідник: модуль перенесено в autocut/core/pipeline.py (старий імпорт shorts.pipeline працює далі)."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("autocut.core.pipeline")
