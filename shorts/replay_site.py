"""Перехідник: модуль перенесено в autocut/eu/replay_site.py (старий імпорт shorts.replay_site працює далі)."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("autocut.eu.replay_site")
