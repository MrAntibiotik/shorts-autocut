"""Перехідник: модуль перенесено в autocut/eu/replay_record.py (старий імпорт shorts.replay_record працює далі)."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("autocut.eu.replay_record")
