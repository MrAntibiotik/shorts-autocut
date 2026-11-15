"""Перехідник: модуль перенесено в autocut/eu/poster_link.py (старий імпорт shorts.poster_link працює далі)."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("autocut.eu.poster_link")
