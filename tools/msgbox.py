"""Перехідник: код перенесено в autocut/app/msgbox.py.

Старий шлях tools\\msgbox.py лишено для extra\\*.bat, кнопки DaVinci, автозапуску бота і ярликів.
"""
import importlib
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    runpy.run_module("autocut.app.msgbox", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = importlib.import_module("autocut.app.msgbox")
