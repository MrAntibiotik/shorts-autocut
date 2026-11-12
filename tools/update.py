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
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "MrAntibiotik/shorts-autocut"
BRANCH = "main"
VERSION_FILE = ROOT / "VERSION.json"
UA = {"User-Agent": "shorts-autocut-updater"}
SKIP_TOP = {"config", "samples", "venv", "work", "output", "models", "ffmpeg", "whisper", "defaults"}
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def http(url: str, timeout: float = 60) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def local_version() -> dict:
    try:
        return json.loads(VERSION_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def remote_version() -> dict:
    """Останній коміт гілки: {sha, date, message}."""
    d = json.loads(http(f"https://api.github.com/repos/{REPO}/commits/{BRANCH}", 30))
    c = d.get("commit") or {}
    return {"sha": d["sha"], "date": (c.get("committer") or {}).get("date", ""),
            "message": (c.get("message") or "").split("\n")[0]}


def changes_since(old_sha: str | None, limit: int = 10) -> list[str]:
    """Заголовки нових комітів (для повідомлення «що нового»)."""
    if not old_sha:
        return []
    try:
        d = json.loads(http(f"https://api.github.com/repos/{REPO}/compare/{old_sha}...{BRANCH}", 30))
        return [(c["commit"]["message"] or "").split("\n")[0] for c in d.get("commits", [])][-limit:]
    except Exception:
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
