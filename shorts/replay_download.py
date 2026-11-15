"""Перехідник: модуль перенесено в autocut/eu/replay_download.py (старий імпорт shorts.replay_download працює далі)."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("autocut.eu.replay_download")
