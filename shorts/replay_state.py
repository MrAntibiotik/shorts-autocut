"""Перехідник: модуль перенесено в autocut/eu/replay_state.py (старий імпорт shorts.replay_state працює далі)."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("autocut.eu.replay_state")
