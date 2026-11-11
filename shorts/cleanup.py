"""Автоматичне прибирання робочої папки (work).

Що видаляється:
  * застарілий кеш того самого запису — після зміни рамок чи налаштувань кеш створюється в новій
    папці (vision_wot_XXXX / audio_XXXX), а стара вже ніколи не знадобиться;
  * кеш записів, які не обробляли довше keep_days днів;
  * тимчасові файли старих завдань (work/jobs/job_…) і старі логи — теж старші за keep_days.

Що НЕ чіпається: оригінальні записи, готові шортси (папка output), налаштування, профілі,
кеш запису, який обробляється зараз.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import time
from pathlib import Path

from .config import work_dir

log = logging.getLogger("shorts")

_CACHE_RE = re.compile(r"^(vision_[A-Za-z0-9]+|audio|speech)_[0-9a-f]{8}$")
_VIDEO_DIR_RE = re.compile(r"^.+_[0-9a-f]{6}$")


def _cfg(settings: dict) -> tuple[bool, float]:
    c = settings.get("cleanup", {}) or {}
    return bool(c.get("enabled", True)), float(c.get("keep_days", 14))


def _size(p: Path) -> int:
    try:
        if p.is_file():
            return p.stat().st_size
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    except OSError:
        return 0


def _newest_mtime(p: Path) -> float:
    """Коли папкою/файлом користувались востаннє (найновіший файл усередині)."""
    try:
        t = p.stat().st_mtime
        if p.is_dir():
            for f in p.rglob("*"):
                try:
                    t = max(t, f.stat().st_mtime)
                except OSError:
                    pass
        return t
    except OSError:
        return time.time()


def _remove(p: Path) -> int:
    n = _size(p)
    try:
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        return n
    except OSError as e:
        log.debug("Не вдалося видалити %s: %s", p, e)
        return 0


def _mb(n: int) -> str:
    return f"{n / 1024 / 1024:.0f} МБ" if n < 1024 ** 3 else f"{n / 1024 ** 3:.1f} ГБ"


def drop_stale_siblings(settings: dict, current: Path, drop: bool = True) -> None:
    """Поточний кеш запису = current (…/<запис>/vision_wot_ab12cd34). Видаляє інші кеші того ж типу
    для цього запису (зі старими рамками/налаштуваннями) — вони вже не знадобляться.
    drop=False — лише позначити «користувались щойно» (для звуку: кеш «з розділенням» і «без» —
    обидва корисні, перемикання галочки не має стирати хвилини роботи Demucs)."""
    for d in (current, current.parent):      # «користувались щойно» — щоб не видалило як старе
        try:
            os.utime(d)
        except OSError:
            pass
    enabled, _ = _cfg(settings)
    m = _CACHE_RE.match(current.name)
    if not drop or not enabled or not m:
        return
    freed, n = 0, 0
    for d in current.parent.iterdir():
        mm = _CACHE_RE.match(d.name)
        if d.is_dir() and d != current and mm and mm.group(1) == m.group(1):
            freed += _remove(d)
            n += 1
    if n:
        log.info("Прибирання: видалено застарілий кеш цього запису (%d папок, %s)", n, _mb(freed))


def cleanup_work(settings: dict, keep: list[Path] | None = None) -> None:
    """Видаляє все в work, чим не користувались довше keep_days днів. Помилки не зупиняють роботу."""
    enabled, days = _cfg(settings)
    if not enabled:
        return
    try:
        root = work_dir(settings)
        limit = time.time() - days * 86400
        keep_set = {Path(k).resolve() for k in (keep or [])}
        freed, n = 0, 0

        def old(p: Path) -> bool:
            return p.resolve() not in keep_set and _newest_mtime(p) < limit

        # кеш записів: <назва запису>_<6 символів>/
        for d in root.iterdir():
            if d.is_dir() and _VIDEO_DIR_RE.match(d.name) and d.name not in ("jobs", "logs", "preview") \
                    and any(_CACHE_RE.match(x.name) for x in d.iterdir() if x.is_dir()) and old(d):
                freed += _remove(d)
                n += 1
        # тимчасові файли старих завдань
        jobs = root / "jobs"
        if jobs.is_dir():
            for d in jobs.iterdir():
                if d.name.startswith("job_") and old(d):
                    freed += _remove(d)
                    n += 1
        # старі логи
        logs = root / "logs"
        if logs.is_dir():
            for f in logs.iterdir():
                if f.is_file() and f.suffix in (".log", ".txt") and old(f):
                    freed += _remove(f)
                    n += 1
        if n:
            log.info("Прибирання: видалено старі тимчасові файли (%d шт., %s), старші за %g дн.",
                     n, _mb(freed), days)
    except Exception as e:     # прибирання ніколи не має зламати основну роботу
        log.debug("Прибирання пропущено: %s", e)


# ------------------------------------------------------------------ ручне очищення (кнопка)
def _cache_targets(settings: dict, with_reports: bool) -> list[Path]:
    from .config import output_dir
    root = work_dir(settings)
    out: list[Path] = []
    for d in root.iterdir():
        if d.is_dir() and _VIDEO_DIR_RE.match(d.name) and d.name not in ("jobs", "logs"):
            out.append(d)                                   # кеш розпізнавання записів
    for name in ("preview", "zone_frame.png"):
        if (root / name).exists():
            out.append(root / name)
    jobs = root / "jobs"
    if jobs.is_dir():
        out += [d for d in jobs.iterdir() if d.is_dir() and d.name.startswith("job_")]
    logs = root / "logs"
    if logs.is_dir():
        busy = {Path(h.baseFilename).resolve() for lg in (logging.getLogger(), log)
                for h in lg.handlers if hasattr(h, "baseFilename")}       # лог поточного запуску
        out += [f for f in logs.iterdir() if f.is_file() and f.resolve() not in busy]
    if with_reports:
        o = output_dir(settings)
        for name in ("debug", "reports", "audio"):          # картинки test_vision, звіти, тестові доріжки
            if (o / name).is_dir():
                out.append(o / name)
        out += list(o.glob("preview_*.png")) + list(o.glob("zones_*.png"))
    return out


def cache_size(settings: dict, with_reports: bool = True) -> int:
    return sum(_size(p) for p in _cache_targets(settings, with_reports))


def clear_cache(settings: dict, with_reports: bool = True) -> tuple[int, int]:
    """Видаляє весь кеш (і, за бажанням, звіти/debug). Готові шортси, налаштування й рамки не чіпає.
    Повертає (скільки звільнено байт, скільки не вдалося видалити — напр. файл зайнятий)."""
    freed, failed = 0, 0
    for p in _cache_targets(settings, with_reports):
        n = _remove(p)
        if p.exists():
            failed += 1
        freed += n
    log.info("Кеш очищено: звільнено %s%s", _mb(freed), f", не видалено {failed} (зайняті)" if failed else "")
    return freed, failed


def human_size(n: int) -> str:
    return _mb(n)
