"""Перехідник: модуль перенесено в autocut/eu/replay_filter.py (старий імпорт shorts.replay_filter працює далі)."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("autocut.eu.replay_filter")
