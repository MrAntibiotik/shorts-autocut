"""Швидке оновлення інструмента з GitHub (без перевстановлення Python і PyTorch).

Робить те саме, що крок «файли інструмента» в SETUP.bat:
  * завантажує свіжий код з GitHub (MrAntibiotik/shorts-autocut, гілка main);
  * замінює код; config і samples — лише додає нові файли (ваші рамки, налаштування, токен бота не чіпає);
  * оновлює профілі ігор зі збереженням ваших рамок; стандартні профілі — для «Скинути налаштування…»;
  * якщо змінився requirements.txt — доставляє бібліотеки;
  * оновлює кнопку в DaVinci.
Запуск: кнопка «🔄 Оновити» в Telegram-боті (/update) або extra\\update.bat.
Повний SETUP.bat потрібен лише якщо щось зламалось (драйвер, Python, PyTorch, ffmpeg).

Версії й відкат:
  * перед кожним оновленням поточний код зберігається в backups\\<дата>_<версія>\\ (останні KEEP_BACKUPS);
  * restore_backup() повертає код з такої копії (налаштування, рамки, токен бота, відео й кеш не чіпає);
  * install_ref(sha) — поставити будь-яку версію з GitHub (якщо локальної копії вже немає).
  update.py --backups | --rollback [назва] | --install <sha>
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
SKIP_TOP = {"config", "samples", "venv", "work", "output", "output_replays_en", "models", "ffmpeg", "whisper", "defaults",
            "backups"}
BACKUP_DIR = ROOT / "backups"
KEEP_BACKUPS = 5
# що НЕ є кодом програми (не копіюється в резервну копію й не замінюється при відкаті)
NOT_CODE = SKIP_TOP | {"VERSION.json", ".git", "__pycache__", ".venv"}


def code_version(root: Path | None = None) -> str:
    """Номер версії з autocut/version.py (без імпорту — щоб бачити саме файл на диску)."""
    import re
    root = root or ROOT
    try:
        txt = (root / "autocut" / "version.py").read_text(encoding="utf-8")
        m = re.search(r'VERSION\s*=\s*"([^"]+)"', txt)
        return m.group(1) if m else "?"
    except OSError:
        return "1.x"


def _code_items(root: Path) -> list[Path]:
    out = []
    for item in sorted(root.iterdir()):
        if item.name in NOT_CODE or item.name.startswith(".") or item.name.endswith((".tmp", ".part")):
            continue
        if item.is_dir() and not any(item.rglob("*")):
            continue
        out.append(item)
    return out


def _ignore(_d, names):
    return [n for n in names if n == "__pycache__" or n.endswith((".pyc", ".tmp", ".part"))]


def backup_current(reason: str = "перед оновленням") -> Path | None:
    """Зберігає поточний код у backups\\<дата>_<версія>\\ (+ meta.json). Залишає KEEP_BACKUPS найновіших."""
    import time
    lv = local_version()
    ver = code_version()
    name = time.strftime("%Y%m%d-%H%M%S") + f"_v{ver}" + (f"_{lv['sha'][:7]}" if lv.get("sha") else "")
    dst = BACKUP_DIR / name
    k = 2
    while dst.exists():                      # дві копії за одну секунду (відкат одразу після оновлення)
        dst = BACKUP_DIR / f"{name}-{k}"
        k += 1
    name = dst.name
    tmp = BACKUP_DIR / (name + ".part")
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    for item in _code_items(ROOT):
        if item.is_dir():
            shutil.copytree(item, tmp / item.name, ignore=_ignore)
        else:
            shutil.copy2(item, tmp / item.name)
    meta = {"version": ver, "sha": lv.get("sha", ""), "message": lv.get("message", ""), "date": lv.get("date", ""),
            "created": time.strftime("%Y-%m-%d %H:%M"), "reason": reason}
    (tmp / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.rename(dst)
    for old in list_backups()[KEEP_BACKUPS:]:
        shutil.rmtree(old["path"], ignore_errors=True)
    return dst


def list_backups() -> list[dict]:
    """Збережені версії, новіші першими: [{name, path, version, sha, message, created}]."""
    out = []
    if not BACKUP_DIR.is_dir():
        return out
    for d in BACKUP_DIR.iterdir():
        if not d.is_dir() or d.name.endswith(".part"):
            continue
        try:
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({**meta, "name": d.name, "path": d})
    return sorted(out, key=lambda x: x["name"], reverse=True)


def _after_code_change(req_before: str, log=print) -> bool:
    """Бібліотеки (якщо змінився requirements.txt) і кнопка DaVinci — після заміни коду."""
    req_changed = _sha(ROOT / "requirements.txt") != req_before
    if req_changed:
        log("Змінився список бібліотек — доставляю…")
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(ROOT / "requirements.txt")],
                       creationflags=_NO_WINDOW)
    subprocess.run([sys.executable, str(ROOT / "tools" / "install_resolve.py")], capture_output=True,
                   creationflags=_NO_WINDOW)
    return req_changed


def restore_backup(name: str | None = None, log=print) -> dict:
    """Відкат на збережену версію (name — з list_backups(); None — найновіша збережена).
    Поточна версія перед тим теж зберігається — відкат можна скасувати."""
    items = list_backups()
    b = next((x for x in items if x["name"] == name), None) if name else (items[0] if items else None)
    if b is None:
        raise RuntimeError("Збережених версій немає" if not items else f"Немає збереженої версії {name}")
    src = Path(b["path"])
    req_before = _sha(ROOT / "requirements.txt")
    log(f"Зберігаю поточну версію {code_version()}…")
    backup_current(f"перед відкатом на {b['version']}")
    log(f"Повертаю версію {b['version']}…")
    for item in src.iterdir():
        if item.name == "meta.json":
            continue
        t = ROOT / item.name
        if item.is_dir():
            if t.exists():
                shutil.rmtree(t)
            shutil.copytree(item, t)
        else:
            shutil.copy2(item, t)
    req_changed = _after_code_change(req_before, log)
    VERSION_FILE.write_text(json.dumps({k: b.get(k, "") for k in ("sha", "date", "message")} | {"version": b["version"]},
                                       ensure_ascii=False, indent=1), encoding="utf-8")
    return {"version": b["version"], "sha": b.get("sha", ""), "message": b.get("message", ""),
            "requirements": req_changed}
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


def install_ref(sha: str, log=print) -> dict:
    """Поставити конкретну версію з GitHub (коміт sha) — напр. якщо локальної копії вже немає."""
    msg = next((it["message"] for it in _safe_feed() if it["sha"].startswith(sha)), "")
    return apply_update(log, ref={"sha": sha, "date": "", "message": msg or f"версія {sha[:7]}"})


def _safe_feed() -> list[dict]:
    try:
        return feed()
    except Exception:  # noqa: BLE001
        return []


def apply_update(log=print, ref: dict | None = None) -> dict:
    """Оновлює файли (ref — конкретний коміт замість найновішого). Повертає {sha, message, changes, files, …}.
    Перед заміною коду поточна версія зберігається в backups\\."""
    old = local_version().get("sha")
    rv = ref or remote_version()
    if old == rv["sha"]:
        return {"up_to_date": True, **rv}
    if old or (ROOT / "autocut").is_dir() or (ROOT / "shorts").is_dir():
        log("Зберігаю поточну версію (для відкату)…")
        try:
            backup_current()
        except OSError as e:
            log(f"Резервну копію не зроблено: {e}")
    log("Завантажую код з GitHub…")
    url = (f"https://github.com/{REPO}/archive/{rv['sha']}.zip" if ref else
           f"https://github.com/{REPO}/archive/refs/heads/{BRANCH}.zip")
    data = http(url, 120)
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
    req_changed = _after_code_change(req_before, log)
    changes = changes_since(old) if not ref else []
    rv = {**rv, "version": code_version()}
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
        VERSION_FILE.write_text(json.dumps({**remote_version(), "version": code_version()}, ensure_ascii=False,
                                           indent=1), encoding="utf-8")
        return 0
    if "--backups" in sys.argv:
        print(f"Поточна версія: {code_version()}")
        for i, b in enumerate(list_backups(), 1):
            print(f"  {i}. {b['version']}  ({b['created']})  {b.get('message', '')[:70]}  [{b['name']}]")
        return 0
    if "--rollback" in sys.argv:
        i = sys.argv.index("--rollback")
        arg = sys.argv[i + 1] if len(sys.argv) > i + 1 else None
        items = list_backups()
        if arg is None:
            if not items:
                print("Збережених версій немає.")
                return 1
            print(f"Поточна версія: {code_version()}. Збережені:")
            for k, b in enumerate(items, 1):
                print(f"  {k}. {b['version']}  ({b['created']})  {b.get('message', '')[:70]}")
            ans = input("Номер версії для відкату (Enter — скасувати): ").strip()
            if not ans.isdigit() or not 1 <= int(ans) <= len(items):
                return 0
            arg = items[int(ans) - 1]["name"]
        elif arg.isdigit() and 1 <= int(arg) <= len(items):
            arg = items[int(arg) - 1]["name"]
        res = restore_backup(arg)
        print(f"Повернуто версію {res['version']}. Перезапустіть вікно програми й бота.")
        return 0
    if "--install" in sys.argv:
        res = install_ref(sys.argv[sys.argv.index("--install") + 1])
        print(f"Встановлено {res['sha'][:7]}: {res['message']}")
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
