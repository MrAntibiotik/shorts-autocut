"""Увесь відео-контент — в одній папці, розбитій по каналах:

  Робочий стіл\\Контент\\   (якщо робочий стіл у OneDrive — «Відео»\\Контент + ярлик «Контент» на робочому столі,
                          щоб гігабайти відео не синхронізувались у хмару)
    Мій канал\\   Нарізки (результати автонарізки: шортси, звіти; там же _видалені) · Хайлайти · До публікації
    EU-канал\\    Нарізки (шортси з реплеїв) · Записи реплеїв (OBS)

Шлях до «Контент» можна змінити (paths.content_dir у config\\settings.json або у вікні програми).
Кеш (work\\), моделі, налаштування — лишаються в папці програми: це не контент.

migrate_legacy() — один раз переносить старі папки (C:\\ShortsTool\\output, Робочий стіл\\Хайлайти,
C:\\ShortsTool\\output_replays_en, Робочий стіл\\wot replays eu) сюди й переписує шляхи у файлах обліку.
Poster дізнається про перенесення з %LOCALAPPDATA%\\MrAntibiotik\\cutter.json і сам переводить свої папки.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from pathlib import Path

log = logging.getLogger("content")

CONTENT = "Контент"
MAIN, EU = "Мій канал", "EU-канал"
FOLDERS = {
    "main_cuts": (MAIN, "Нарізки"),
    "highlights": (MAIN, "Хайлайти"),
    "publish": (MAIN, "До публікації"),
    "eu_cuts": (EU, "Нарізки"),
    "eu_records": (EU, "Записи реплеїв"),
}
LEGACY_OUTPUT = ("", "output")
LEGACY_EU = ("", "output_replays_en")
MIGRATED = ".перенесено_v1"


def in_onedrive(p: Path) -> bool:
    """Папка синхронізується з OneDrive (робочий стіл, перенесений у хмару) — відео туди класти не можна."""
    return any(part.lower().startswith("onedrive") for part in Path(p).parts)


def content_dir(settings: dict) -> Path:
    """Робочий стіл\\Контент — якщо робочий стіл НЕ в OneDrive. Якщо в OneDrive — файли в «Відео»\\Контент
    (поза хмарою, поруч зі стрімами), а на робочому столі — лише ярлик «Контент» (desktop_shortcut)."""
    from autocut.core.config import desktop_dir, resolve_path
    d = ((settings.get("paths") or {}).get("content_dir") or "").strip()
    if d:
        return resolve_path(d)
    desk = desktop_dir()
    if not in_onedrive(desk):
        return desk / CONTENT
    for base in (videos_dir(), Path.home()):
        if not in_onedrive(base):
            return base / CONTENT
    return Path(Path.home().anchor or "C:\\") / CONTENT


def videos_dir() -> Path:
    """Справжня папка «Відео» (Windows може її перенести)."""
    if os.name == "nt":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(260)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0x0E, None, 0, buf) == 0 and buf.value:  # CSIDL_MYVIDEO
                return Path(buf.value)
        except Exception:  # noqa: BLE001
            pass
    return Path.home() / "Videos"


def desktop_shortcut(settings: dict) -> Path | None:
    """Ярлик «Контент» на робочому столі, якщо сама папка — не на ньому (робочий стіл у OneDrive)."""
    from autocut.core.config import desktop_dir
    target = content_dir(settings)
    desk = desktop_dir()
    if target.parent == desk or os.name != "nt":
        return None
    lnk = desk / f"{CONTENT}.lnk"
    if lnk.exists():
        return lnk
    target.mkdir(parents=True, exist_ok=True)
    import subprocess
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
          f"$s.TargetPath='{target}';$s.Description='Усе відео: Мій канал і EU-канал';$s.Save()")
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=30,
                       creationflags=0x08000000)
    except Exception as e:  # noqa: BLE001
        log.warning("Ярлик «%s» на робочому столі не створено: %s", CONTENT, e)
        return None
    return lnk if lnk.exists() else None


def folder(settings: dict, which: str, create: bool = True) -> Path:
    ch, sub = FOLDERS[which]
    d = content_dir(settings) / ch / sub
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def tree(settings: dict) -> dict[str, Path]:
    return {k: folder(settings, k, create=False) for k in FOLDERS}


# ====================================================================== перенесення старого
def _legacy_sources(settings: dict, bot_cfg: dict | None) -> list[tuple[Path, str]]:
    """[(стара папка, куди)] — лише ті, що реально існують і ще є типовими (не вибрані вручну)."""
    from autocut.core.config import ROOT, desktop_dir
    p = settings.get("paths") or {}
    out = []
    if str(p.get("output_dir") or "") in LEGACY_OUTPUT:
        out.append((ROOT / "output", "main_cuts"))
    out.append((desktop_dir() / "Хайлайти", "highlights"))
    if str(p.get("replays_output_dir") or "") in LEGACY_EU:
        out.append((ROOT / "output_replays_en", "eu_cuts"))
    out.append((desktop_dir() / "wot replays eu", "eu_records"))
    return [(src, k) for src, k in out if src.is_dir() and any(src.iterdir())]


def _unique(p: Path) -> Path:
    n, out = 2, p
    while out.exists():
        out = p.with_name(f"{p.stem} ({n}){p.suffix}")
        n += 1
    return out


def _move_children(src: Path, dst: Path) -> int:
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for item in list(src.iterdir()):
        target = dst / item.name
        if target.exists() and item.is_dir() and target.is_dir():
            n += _move_children(item, target)              # злиття папок з однаковою назвою
            try:
                item.rmdir()
            except OSError:
                pass
            continue
        shutil.move(str(item), str(_unique(target)))
        n += 1
    return n


def _variants(p: Path) -> list[str]:
    s = str(p)
    return list(dict.fromkeys([s, s.replace("\\", "/"), s.replace("/", "\\")]))


def rewrite_value(v, moves: list[tuple[Path, Path]]):
    """Рекурсивно замінює префікси шляхів у рядках json (старе → нове)."""
    if isinstance(v, dict):
        return {k: rewrite_value(x, moves) for k, x in v.items()}
    if isinstance(v, list):
        return [rewrite_value(x, moves) for x in v]
    if isinstance(v, str):
        for old, new in moves:
            for o in _variants(old):
                if v == o or v.startswith(o + "\\") or v.startswith(o + "/") or (
                        os.name == "nt" and v.lower().startswith(o.lower())
                        and v[len(o):len(o) + 1] in ("", "\\", "/")):
                    return str(new) + v[len(o):]
    return v


def rewrite_json_file(f: Path, moves: list[tuple[Path, Path]]) -> bool:
    try:
        data = json.loads(f.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return False
    new = rewrite_value(data, moves)
    if new == data:
        return False
    tmp = f.with_suffix(f.suffix + ".tmp")
    tmp.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(f)
    return True


def rewrite_text_file(f: Path, moves: list[tuple[Path, Path]]) -> bool:
    try:
        t = f.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    n = rewrite_value(t, moves)
    if n != t:
        f.write_text(n, encoding="utf-8")
        return True
    return False


def ensure_migrated(settings: dict, bot_cfg: dict | None = None, replays_dir: Path | None = None,
                    log_fn=None) -> list[tuple[Path, Path]]:
    """Безпечний запуск перенесення: не під час обробки, не двічі одночасно (бот і вікно). Помилка — у лог."""
    root = content_dir(settings)
    try:
        desktop_shortcut(settings)                         # робочий стіл у OneDrive — там лише ярлик
    except Exception as e:  # noqa: BLE001
        log.warning("Ярлик: %s", e)
    if (root / MIGRATED).exists():
        publish_for_poster(settings, bot_cfg)              # Poster завжди знає актуальні папки
        return []
    try:
        from autocut.core.resolve_export import read_status
        st = read_status(settings) or {}
        if st.get("status") == "running" and time.time() - float(st.get("heartbeat") or 0) < 120:
            return []                                          # іде обробка — перенесемо наступного разу
    except Exception:  # noqa: BLE001
        pass
    root.mkdir(parents=True, exist_ok=True)
    lock = root / ".переношу"
    try:
        if lock.exists() and time.time() - lock.stat().st_mtime > 3600:
            lock.unlink()
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except OSError:
        return []                                              # переносить інший процес
    try:
        return migrate_legacy(settings, bot_cfg, replays_dir, log_fn)
    except Exception as e:  # noqa: BLE001
        log.warning("Перенесення відео в «Контент» не вдалося: %s", e)
        return []
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def migrate_legacy(settings: dict, bot_cfg: dict | None = None, replays_dir: Path | None = None,
                   log_fn=None) -> list[tuple[Path, Path]]:
    """Один раз переносить старі папки з відео в «Контент» і переписує шляхи. Повертає [(було, стало)]."""
    say = log_fn or log.info
    root = content_dir(settings)
    marker = root / MIGRATED
    if marker.exists():
        return []
    moves: list[tuple[Path, Path]] = []
    for src, which in _legacy_sources(settings, bot_cfg):
        dst = folder(settings, which)
        if src.resolve() == dst.resolve():
            continue
        say(f"Переношу {src} → {dst}")
        _move_children(src, dst)
        moves.append((src, dst))
        try:
            src.rmdir()
        except OSError:
            pass
    # «До публікації» (стара типова — output\_до_публікації, тепер у Нарізки\) — у свою папку каналу,
    # якщо бот публікує саме туди (або папку публікації не вибирали)
    from autocut.core.config import ROOT
    legacy_pub = ROOT / "output" / "_до_публікації"
    old_pub = folder(settings, "main_cuts") / "_до_публікації"
    cfg_pub = str((bot_cfg or {}).get("publish_dir") or "")
    new_pub = None
    if old_pub.is_dir() and (not cfg_pub or Path(cfg_pub) in (legacy_pub, old_pub)):
        new_pub = folder(settings, "publish")
        say(f"Переношу {old_pub} → {new_pub}")
        _move_children(old_pub, new_pub)
        try:
            old_pub.rmdir()
        except OSError:
            pass
        moves += [(legacy_pub, new_pub), (old_pub, new_pub)]
    moves.sort(key=lambda m: -len(str(m[0])))           # довший (точніший) шлях — першим
    if new_pub is not None and bot_cfg is not None:
        bot_cfg["publish_dir"] = str(new_pub)
    if moves:
        n = 0
        for d in (folder(settings, "main_cuts"), folder(settings, "eu_cuts"), folder(settings, "highlights")):
            for f in list(d.glob("job_*/job.json")) + list(d.glob("_*.json")) + list(d.glob("*.json")):
                n += rewrite_json_file(f, moves)
        if replays_dir and Path(replays_dir).is_dir():
            for f in Path(replays_dir).glob("*.recorded"):
                n += rewrite_text_file(f, moves)
        say(f"Шляхи у файлах обліку оновлено: {n}")
    root.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M"),
                                  "moves": [[str(a), str(b)] for a, b in moves]}, ensure_ascii=False, indent=1),
                      encoding="utf-8")
    publish_for_poster(settings, bot_cfg, moves)
    return moves


# ====================================================================== для Poster
def discovery_file() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".config")
    return Path(base) / "MrAntibiotik" / "cutter.json"


def publish_for_poster(settings: dict, bot_cfg: dict | None = None, moves: list | None = None) -> None:
    """%LOCALAPPDATA%\\MrAntibiotik\\cutter.json: папки програми нарізки + історія перенесень (було → стало).
    Poster бере звідси папку хайлайтів і переводить свої налаштування/черги на нові шляхи."""
    f = discovery_file()
    try:
        old = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    hist = old.get("moved") or []
    for a, b in moves or []:
        if [str(a), str(b)] not in hist:
            hist.append([str(a), str(b)])
    pub = (bot_cfg or {}).get("publish_dir") or str(folder(settings, "publish", create=False))
    data = {"version": 1, "content": str(content_dir(settings)),
            "folders": {k: str(v) for k, v in tree(settings).items()}, "publish": str(pub), "moved": hist}
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(f)
    except OSError as e:
        log.warning("cutter.json не записано: %s", e)
