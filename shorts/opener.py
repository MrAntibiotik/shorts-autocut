"""Перехідник: модуль перенесено в autocut/main/opener.py (старий імпорт shorts.opener працює далі)."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("autocut.main.opener")
