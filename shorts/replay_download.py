"""Реплеї для запису: імпорт із папки завантажень браузера або скачування за посиланням з фільтром.

1) Імпорт (працює з будь-яким сайтом, напр. wot-record.com): ви переглядаєте сайт з фільтром і тиснете «Download»
   на реплеях, що сподобались; інструмент забирає .wotreplay з папки «Завантаження», перевіряє версію гри,
   шкоду й фраги і переносить у папку реплеїв.
2) Скачування за посиланням — лише з сайтів, які це дозволяють: спершу читається robots.txt сайту, і якщо
   автоматичний доступ до списку чи скачування там заборонено (як на wot-record.com) — нічого не скачується.
   Інструмент:
  • проходить сторінки списку (page/2/ або ?page=2), збирає номери реплеїв;
  • пропускає вже скачані (журнал downloaded.json у папці реплеїв);
  • скачує файл, перевіряє, що це справді реплей, і читає його дані (replay_meta);
  • залишає лише ті, що відкриються на вашому клієнті (та сама версія гри) і дограні до кінця;
  • між запитами робить паузу (за замовчуванням 3 с), щоб не навантажувати сайт.

Лише стандартна бібліотека Python (urllib), без додаткових пакетів.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass, field
from pathlib import Path

from .replay_meta import ReplayError, ReplayInfo, parse, same_version

log = logging.getLogger("replays")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ShortsAutocut/1.0 (replay downloader)"
ID_RE = re.compile(r"(?:^|[/\"'])(?:site|replay)/(?:download/)?(\d{4,})")
PAGE_RE = re.compile(r"/page/(\d+)/?")
QPAGE_RE = re.compile(r"([?&])page=\d+")
LOG_NAME = "downloaded.json"


@dataclass
class DownloadOptions:
    url: str
    dest: Path
    count: int = 10                    # скільки нових підхожих реплеїв потрібно
    client_version: tuple = ()         # (1, 26, 0) — версія вашої гри; () — не перевіряти
    min_damage: int = 0
    min_kills: int = 0
    pause_s: float = 3.0
    max_pages: int = 10
    timeout_s: float = 60.0


@dataclass
class DownloadResult:
    saved: list[ReplayInfo] = field(default_factory=list)
    skipped: dict = field(default_factory=dict)     # причина -> кількість
    errors: list[str] = field(default_factory=list)

    def skip(self, why: str):
        self.skipped[why] = self.skipped.get(why, 0) + 1


class Cancelled(Exception):
    pass


class Blocked(Exception):
    """Сайт забороняє автоматичний доступ (robots.txt)."""


def base_of(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    return f"{p.scheme or 'https'}://{p.netloc}"


def path_style(url: str) -> bool:
    """wotreplays: сторінки як …/page/2/; інші сайти (wot-record): ?page=2."""
    return "/site/" in urllib.parse.urlsplit(url).path or bool(PAGE_RE.search(url))


def page_url(url: str, n: int) -> str:
    """Адреса n-ї сторінки списку з тим самим фільтром."""
    url = url.split("#")[0]
    if n <= 1:
        return url
    if not path_style(url):
        if QPAGE_RE.search(url):
            return QPAGE_RE.sub(lambda m: f"{m.group(1)}page={n}", url, count=1)
        return url + ("&" if "?" in url else "?") + f"page={n}"
    if PAGE_RE.search(url):
        return PAGE_RE.sub(f"/page/{n}/", url, count=1)
    path, _, query = url.partition("?")
    return f"{path.rstrip('/')}/page/{n}/" + (f"?{query}" if query else "")


def replay_ids(html: str) -> list[str]:
    """Номери реплеїв на сторінці списку — по порядку, без повторів."""
    out = []
    for m in ID_RE.finditer(html or ""):
        if m.group(1) not in out:
            out.append(m.group(1))
    return out


def download_url(base: str, rid: str, list_url: str = "") -> str:
    if not list_url or path_style(list_url):
        return f"{base}/site/download/{rid}"
    return f"{base}/download/{rid}"


def robots_check(url: str, getter, timeout: float, *urls: str) -> None:
    """Перевіряє robots.txt сайту: якщо автоматичний доступ до списку чи скачування заборонено — Blocked."""
    base = base_of(url)
    try:
        text = getter(f"{base}/robots.txt", timeout).decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError):
        return  # robots.txt немає — обмежень не оголошено
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(text.splitlines())
    for u in (url, *urls):
        if not rp.can_fetch(UA, u):
            raise Blocked(f"Сайт {urllib.parse.urlsplit(base).netloc} забороняє автоматичний доступ "
                          f"({urllib.parse.urlsplit(u).path.split('/')[1] or '/'}… у robots.txt). "
                          "Скачуйте реплеї з нього вручну кнопкою «Download» у браузері — вкладка сама забере їх "
                          "з папки «Завантаження» (поле «Забирати з папки»).")


def check_replay(path: Path, opts) -> tuple[ReplayInfo | None, str | None]:
    """Читає реплей і перевіряє фільтри. (дані, причина пропуску або None)."""
    try:
        info = parse(path)
    except ReplayError as e:
        return None, f"не реплей ({e})"
    if opts.client_version and not same_version(info.version_tuple, opts.client_version):
        return info, f"інша версія гри ({info.version or '?'})"
    if not info.complete:
        return info, "бій не дограно до кінця"
    if info.damage < opts.min_damage:
        return info, f"шкода менша за {opts.min_damage}"
    if info.kills < opts.min_kills:
        return info, f"фрагів менше за {opts.min_kills}"
    return info, None


@dataclass
class ImportOptions:
    src: Path                          # папка завантажень браузера
    dest: Path                         # папка реплеїв інструмента
    client_version: tuple = ()
    min_damage: int = 0
    min_kills: int = 0
    min_age_s: float = 5.0             # файл, що ще докачується, не чіпаємо


def import_folder(opts: ImportOptions) -> DownloadResult:
    """Забирає .wotreplay з папки завантажень: підхожі переносить у папку реплеїв, решту лишає на місці."""
    src, dest = Path(opts.src), Path(opts.dest)
    dest.mkdir(parents=True, exist_ok=True)
    res = DownloadResult()
    if not src.is_dir():
        res.errors.append(f"Папки {src} немає")
        return res
    now = time.time()
    for p in sorted(src.glob("*.wotreplay")):
        try:
            if now - p.stat().st_mtime < opts.min_age_s:
                continue
        except OSError:
            continue
        info, why = check_replay(p, opts)
        if why:
            res.skip(why)
            log.info("  %s лишаю в папці: %s", p.name, why)
            continue
        rid = re.sub(r"[^\w\-]+", "_", p.stem)[:80]
        target = dest / f"{rid}.wotreplay"
        if target.exists() or target.with_suffix(".recorded").exists():
            res.skip("уже є в папці реплеїв")
            continue
        shutil.move(str(p), str(target))   # працює і між різними дисками
        info.path = str(target)
        (dest / f"{rid}.json").write_text(json.dumps(info.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        res.saved.append(info)
        log.info("  ✓ %s: %s, %s, шкода %d, фрагів %d", p.name, info.vehicle, info.map_name, info.damage, info.kills)
    return res


def load_log(dest: Path) -> dict:
    try:
        return json.loads((dest / LOG_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_log(dest: Path, data: dict) -> None:
    tmp = dest / (LOG_NAME + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(dest / LOG_NAME)


def _get(url: str, timeout: float, referer: str = "") -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": referer or url})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — адреса від користувача
        return r.read()


def fetch(opts: DownloadOptions, progress=None, cancel=None, getter=_get) -> DownloadResult:
    """Скачує до opts.count нових підхожих реплеїв. progress(текст, частка 0..1); cancel() -> bool."""
    dest = Path(opts.dest)
    dest.mkdir(parents=True, exist_ok=True)
    done = load_log(dest)
    res = DownloadResult()
    base = base_of(opts.url)
    robots_check(opts.url, getter, opts.timeout_s, download_url(base, "1", opts.url))

    def say(text: str):
        log.info(text)
        if progress:
            progress(text, min(1.0, len(res.saved) / max(1, opts.count)))

    def wait():
        end = time.time() + opts.pause_s
        while time.time() < end:
            if cancel and cancel():
                raise Cancelled()
            time.sleep(0.2)

    for n in range(1, opts.max_pages + 1):
        if len(res.saved) >= opts.count:
            break
        url = page_url(opts.url, n)
        say(f"Сторінка {n}: {url}")
        try:
            html = getter(url, opts.timeout_s).decode("utf-8", errors="replace")
        except (urllib.error.URLError, OSError) as e:
            res.errors.append(f"сторінка {n}: {e}")
            break
        ids = [i for i in replay_ids(html) if i not in done]
        if not replay_ids(html):
            say("На сторінці немає реплеїв — кінець списку")
            break
        for rid in ids:
            if len(res.saved) >= opts.count:
                break
            if cancel and cancel():
                raise Cancelled()
            wait()
            path = dest / f"{rid}.wotreplay"
            try:
                data = getter(download_url(base, rid, opts.url), opts.timeout_s, url)
            except (urllib.error.URLError, OSError) as e:
                res.errors.append(f"#{rid}: {e}")
                continue
            path.write_bytes(data)
            info, why = check_replay(path, opts)
            done[rid] = {"file": path.name if not why else None, "skip": why, "ts": int(time.time())}
            save_log(dest, done)
            if why:
                path.unlink(missing_ok=True)
                res.skip(why)
                log.info("  #%s пропущено: %s", rid, why)
                continue
            res.saved.append(info)
            (dest / f"{rid}.json").write_text(json.dumps(info.to_dict(), ensure_ascii=False, indent=1),
                                              encoding="utf-8")
            say(f"  ✓ #{rid}: {info.vehicle}, {info.map_name}, шкода {info.damage}, фрагів {info.kills} "
                f"({len(res.saved)}/{opts.count})")
        wait()
    return res


__all__ = ["Blocked", "Cancelled", "DownloadOptions", "DownloadResult", "ImportOptions", "check_replay", "download_url",
           "fetch", "import_folder", "page_url", "replay_ids", "robots_check"]
