"""Скинути налаштування профілю гри до стандартних (те саме, що кнопка у вікні запуску)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shorts.config import ask_profile, reset_profile  # noqa: E402
from shorts.errors import UserError  # noqa: E402


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    name = ask_profile("wot")
    ans = input("Залишити ваші обведені рамки? (т/н) [Enter = т]: ").strip().lower() or "т"
    keep = ans.startswith(("т", "y", "д"))
    path = reset_profile(name, keep_zones=keep)
    print(f"Готово: {path}" + (" (рамки збережено)" if keep else " (рамки скинуто — обведіть знову)"))
    print("Попередня версія — поруч, з розширенням .json.bak")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except UserError as e:
        print(f"\n[ПОМИЛКА] {e}")
        sys.exit(1)
