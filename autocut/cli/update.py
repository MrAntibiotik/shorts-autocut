"""Швидке оновлення інструмента з GitHub (без перевстановлення Python і PyTorch).

Робить те саме, що крок «файли інструмента» в SETUP.bat:
  * завантажує свіжий код з GitHub (MrAntibiotik/shorts-autocut, гілка main);
  * замінює код; config і samples — лише додає нові файли (ваші рамки, налаштування, токен бота не чіпає);
  * оновлює профілі ігор зі збереженням ваших рамок; стандартні профілі — для «Скинути налаштування…»;
  * якщо змінився requirements.txt — доставляє бібліотеки;
  * оновлює кнопку в DaVinci.
Запуск: кнопка «🔄 Оновити» в Telegram-боті (/update) або extra\\update.bat.
Повний SETUP.bat потрібен лише якщо щось зламалось (драйвер, Python, PyTorch, ffmpeg).
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPO = "MrAntibiotik/shorts-autocut"
BRANCH = "main"
VERSION_FILE = ROOT / "VERSION.json"
UA = {"User-Agent": "shorts-autocut-updater"}
SKIP_TOP = {"config", "samples", "venv", "work", "output", "output_replays_en", "models", "ffmpeg", "whisper", "defaults"}
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def http(url: str, timeout: float = 60) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def local_version() -> dict:
    try:
        return json.loads(VERSION_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


_FEED: dict = {"t": 0.0, "items": []}


def feed(max_age_s: float = 60) -> list[dict]:
    """Останні коміти гілки зі стрічки GitHub (…/commits/main.atom) — НЕ рахується в ліміт API
    (60 запитів/год без входу). [{sha, date, message}], новіші першими. Кеш на хвилину."""
    import html as H
    import re
    import time
    import xml.etree.ElementTree as ET
    if time.time() - _FEED["t"] < max_age_s and _FEED["items"]:
        return _FEED["items"]
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(http(f"https://github.com/{REPO}/commits/{BRANCH}.atom", 30))
    items = []
    for e in root.findall("a:entry", ns):
        sha = (e.findtext("a:id", "", ns) or "").rsplit("/", 1)[-1].strip()
        title = " ".join((e.findtext("a:title", "", ns) or "").split())
        if title.startswith("Merge pull request"):     # злиття — беремо назву PR з опису
            body = re.sub(r"<[^>]+>", "", H.unescape(e.findtext("a:content", "", ns) or ""))
            lines = [x.strip() for x in body.splitlines() if x.strip()]
            title = next((x for x in lines[1:] if not x.startswith("Merge pull request")), title)
        if re.fullmatch(r"[0-9a-f]{40}", sha):
            items.append({"sha": sha, "date": e.findtext("a:updated", "", ns) or "", "message": title})
    if not items:
        raise RuntimeError("Порожня стрічка комітів GitHub")
    _FEED.update(t=time.time(), items=items)
    return items


def remote_version() -> dict:
    """Останній коміт гілки: {sha, date, message}. Спершу — стрічка комітів (без ліміту API)."""
    try:
        return dict(feed()[0])
    except Exception:  # noqa: BLE001 — запасний шлях: API (ліміт 60/год)
        pass
    try:
        d = json.loads(http(f"https://api.github.com/repos/{REPO}/commits/{BRANCH}", 30))
    except urllib.error.HTTPError as e:
        if e.code == 403:
            raise RuntimeError("GitHub тимчасово обмежив кількість запитів — спробуйте за годину") from e
        raise
    c = d.get("commit") or {}
    return {"sha": d["sha"], "date": (c.get("committer") or {}).get("date", ""),
            "message": (c.get("message") or "").split("\n")[0]}


def changes_since(old_sha: str | None, limit: int = 10) -> list[str]:
    """Заголовки нових комітів (для повідомлення «що нового») — зі стрічки, без API."""
    if not old_sha:
        return []
    try:
        out = []
        for it in feed():
            if it["sha"] == old_sha:
                break
            if not it["message"].startswith("Merge "):
                out.append(it["message"])
        return list(reversed(out))[-limit:]
    except Exception:  # noqa: BLE001
        return []


def _sha(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest() if p.exists() else ""


def copy_tree(src: Path, dst: Path, only_new: bool = False) -> int:
    n = 0
    for f in src.rglob("*"):
        if f.is_dir():
            continue
        rel = f.relative_to(src)
        t = dst / rel
        if only_new and t.exists():
            continue
        t.parent.mkdir(parents=True, exist_ok=True)
        if not t.exists() or _sha(t) != _sha(f):
            shutil.copy2(f, t)
            n += 1
    return n


def apply_update(log=print) -> dict:
    """Оновлює файли. Повертає {sha, message, changes, files, requirements}."""
    old = local_version().get("sha")
    rv = remote_version()
    if old == rv["sha"]:
        return {"up_to_date": True, **rv}
    log("Завантажую свіжий код з GitHub…")
    data = http(f"https://github.com/{REPO}/archive/refs/heads/{BRANCH}.zip", 120)
    with tempfile.TemporaryDirectory() as tmp:
        zipfile.ZipFile(io.BytesIO(data)).extractall(tmp)
        src = next(Path(tmp).iterdir())
        if not (src / "shorts").is_dir():
            raise RuntimeError("В архіві з GitHub немає файлів інструмента")
        req_before = _sha(ROOT / "requirements.txt")
        n = 0
        for item in src.iterdir():
            if item.name in SKIP_TOP or item.name == "SETUP.bat" and (ROOT / "SETUP.bat").exists():
                continue
            if item.is_dir():
                n += copy_tree(item, ROOT / item.name)
            else:
                if _sha(ROOT / item.name) != _sha(item):
                    shutil.copy2(item, ROOT / item.name)
                    n += 1
        # config, samples — лише нові файли; профілі — злиття з вашими рамками
        n += copy_tree(src / "config", ROOT / "config", only_new=True)
        if (src / "samples").is_dir():
            n += copy_tree(src / "samples", ROOT / "samples", only_new=True)
        py = sys.executable
        subprocess.run([py, str(src / "tools" / "merge_profiles.py"), str(src / "config" / "profiles"),
                        str(ROOT / "config" / "profiles")], creationflags=_NO_WINDOW)
        dflt = ROOT / "defaults" / "profiles"
        if dflt.exists():
            shutil.rmtree(dflt)
        shutil.copytree(src / "config" / "profiles", dflt)
        req_changed = _sha(ROOT / "requirements.txt") != req_before
    if req_changed:
        log("Змінився список бібліотек — доставляю…")
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(ROOT / "requirements.txt")],
                       creationflags=_NO_WINDOW)
    subprocess.run([sys.executable, str(ROOT / "tools" / "install_resolve.py")], capture_output=True,
                   creationflags=_NO_WINDOW)
    changes = changes_since(old)
    VERSION_FILE.write_text(json.dumps(rv, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"up_to_date": False, **rv, "changes": changes, "files": n, "requirements": req_changed,
            "first": old is None}


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if "--mark-current" in sys.argv:          # SETUP.bat щойно поставив свіжий код — запам'ятати версію
        VERSION_FILE.write_text(json.dumps(remote_version(), ensure_ascii=False, indent=1), encoding="utf-8")
        return 0
    res = apply_update()
    if res["up_to_date"]:
        print(f"Уже остання версія ({res['sha'][:7]}: {res['message']}).")
        return 0
    print(f"Оновлено до {res['sha'][:7]}: {res['message']} (файлів змінено: {res['files']}).")
    for c in res.get("changes", []):
        print(f"  • {c}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"[ПОМИЛКА] Оновлення не вдалося: {e}\nСпробуйте SETUP.bat.")
        sys.exit(1)
