"""Очистити кеш інструмента (work) і, за бажанням, звіти/debug-картинки.
Готові шортси (output\\job_…), налаштування, рамки й оригінали не чіпаються."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shorts.cleanup import cache_size, clear_cache, human_size  # noqa: E402
from shorts.config import load_settings  # noqa: E402


def main() -> None:
    st = load_settings()
    print(f"Кеш і тимчасові файли: {human_size(cache_size(st, False))}")
    print(f"Разом зі звітами й debug-картинками: {human_size(cache_size(st, True))}")
    print("Готові шортси, налаштування і рамки НЕ видаляються.\n")
    a = input("Очистити? 1 — лише кеш, 2 — кеш + звіти/debug, інше — скасувати: ").strip()
    if a not in ("1", "2"):
        print("Скасовано.")
        return
    freed, failed = clear_cache(st, with_reports=(a == "2"))
    print(f"Готово: звільнено {human_size(freed)}." + (f" Не видалено {failed} (зайняті іншою програмою)." if failed else ""))
    print("Наступний запуск розпізнає записи заново (довше), далі — знову швидко.")


if __name__ == "__main__":
    main()
