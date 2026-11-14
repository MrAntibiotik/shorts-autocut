"""Режим розробника: вікно з усіма тестами, інструментами, звітами й логами.

Вмикається у вікні запуску: надрукуйте на клавіатурі «antibiotik» (працює і в українській
розкладці — «фтешишщешл»). Після цього в головному вікні з'являється кнопка «Розробник»;
вимкнути — кнопкою в самому вікні розробника.

Тести відкриваються в окремому вікні консолі (як подвійний клік по extra\\*.bat). Якщо в списку
записів у головному вікні виділено запис — він передається тесту автоматично.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shorts.config import CONFIG_DIR, PROFILES_DIR, output_dir, work_dir  # noqa: E402

SECRET = ("antibiotik", "фтешишщешл")      # те саме слово в англійській і українській розкладці

# (текст кнопки, файл у extra, чи передавати запис, підказка)
TESTS = [
    ("Тест розпізнавання екрану", "test_vision.bat", True, "лічильники, повідомлення, таймер, бої + debug-картинки"),
    ("Тест звуку і голосу", "test_audio.bat", True, "розділення голос/гра, звуки, емоції, score"),
    ("Тест мови (експеримент)", "test_speech.bat", True, "Silero VAD + faster-whisper: фрази, SRT"),
    ("Перегляд кадру шортсу", "preview_layout.bat", True, "картинка 1080×1920 до обробки"),
    ("Обвести рамки", "pick_zones.bat", True, "вебка, лічильники, повідомлення, таймер"),
    ("Вирізати зразок звуку", "make_sample.bat", True, "зразки звуків гри для пошуку"),
    ("Перевірити встановлення", "check_setup.bat", False, "Python, ffmpeg, CUDA, бібліотеки"),
]
TOOLS = [
    ("Редагувати субтитри", "edit_subs.bat", False, "виправити фрази й перерендерити MP4"),
    ("Хайлайт будь-якого бою", "make_highlight.bat", False, "16:10 з opener'ом для вибраного бою"),
    ("Перерендерити MP4", "rerender.bat", False, "після зміни заклику"),
    ("Встановити розумну мову", "setup_speech_ai.bat", False, "Silero VAD + faster-whisper"),
    ("Встановити whisper.cpp", "setup_whisper.bat", False, "старі субтитри"),
    ("Перевстановити кнопку DaVinci", "install_resolve.bat", False, "скрипти в Workspace → Scripts"),
    ("Скинути профіль гри", "reset_profile.bat", False, "до стандартних налаштувань"),
    ("Очистити кеш", "clear_cache.bat", False, "кеш, логи, звіти"),
    ("Telegram-бот", "setup_telegram.bat", False, "налаштувати / перезапустити бота"),
    ("Оновити з GitHub", "update.bat", False, "швидке оновлення коду (без SETUP.bat)"),
    ("Вікно запуску з консоллю", "run_job.bat", False, "видно всі повідомлення"),
]


def is_secret(buf: str) -> bool:
    b = buf.lower()
    return any(b.endswith(s) for s in SECRET)


def run_bat(name: str, args: list[str] | None = None) -> None:
    bat = ROOT / "extra" / name
    if not bat.exists():
        messagebox.showerror("Режим розробника", f"Немає файлу {bat}")
        return
    if os.name == "nt":
        subprocess.Popen(["cmd", "/c", str(bat)] + (args or []), cwd=str(ROOT),
                         creationflags=subprocess.CREATE_NEW_CONSOLE)   # type: ignore[attr-defined]
    else:
        print("run:", bat, args)


def open_path(p: Path) -> None:
    if not p.exists():
        messagebox.showinfo("Режим розробника", f"Ще немає: {p}")
        return
    if os.name == "nt":
        os.startfile(str(p))   # type: ignore[attr-defined]
    else:
        print("open:", p)


def newest(folder: Path, patterns: tuple[str, ...]) -> Path | None:
    files = [f for pat in patterns for f in folder.glob(pat) if f.is_file()] if folder.is_dir() else []
    return max(files, key=lambda f: f.stat().st_mtime) if files else None


def status_text(settings: dict) -> str:
    lines = []
    try:
        import json
        prof = json.loads((PROFILES_DIR / "wot.json").read_text(encoding="utf-8-sig"))
        lines.append(f"Профіль WoT: версія {prof.get('profile_version', '?')}, "
                     f"рамки {'підтверджено' if prof.get('zones_confirmed') else 'НЕ підтверджено'}")
    except Exception as e:
        lines.append(f"Профіль WoT: не прочитано ({e})")
    try:
        from shorts.speech_ai import available
        ok, why = available()
        sai = settings.get("speech_ai", {})
        lines.append(f"Розумна мова: {'встановлено' if ok else 'не встановлено'}"
                     + (f" ({sai.get('model')}, {sai.get('device')}/{sai.get('compute_type')})" if ok else ""))
    except Exception:
        pass
    wh = (ROOT / "whisper").exists()
    lines.append(f"whisper.cpp: {'є' if wh else 'немає'}")
    try:
        from shorts.cleanup import cache_size, human_size
        lines.append(f"Кеш і тимчасові файли: {human_size(cache_size(settings, True))}")
    except Exception:
        pass
    jobs = sorted(output_dir(settings).glob("job_*"))
    lines.append(f"Останній результат: {jobs[-1].name if jobs else '—'}")
    return "\n".join(lines)


def open_dev_panel(root: tk.Misc, settings: dict, get_recording, on_disable=None) -> tk.Toplevel:
    """get_recording() -> шлях виділеного (або першого) запису в головному вікні чи None."""
    w = tk.Toplevel(root)
    w.title("Режим розробника")
    w.resizable(True, True)
    pad = {"padx": 8, "pady": 3}
    tk.Label(w, text="🛠 Режим розробника", font=("Segoe UI", 14, "bold")).pack(anchor="w", **pad)

    rec_lbl = tk.Label(w, fg="#555", anchor="w", justify="left", wraplength=690)
    rec_lbl.pack(fill="x", **pad)

    def rec_args(need: bool) -> list[str]:
        r = get_recording() if need else None
        return [r] if r else []

    def refresh():
        r = get_recording()
        rec_lbl.config(text=f"Запис для тестів: {r}" if r else
                       "Запис для тестів: не вибрано (додайте/виділіть у головному вікні або перетягніть у консоль)")
        info.config(text=status_text(settings))

    cols = tk.Frame(w)
    cols.pack(fill="x", padx=8)
    hint_lbl = tk.Label(w, fg="#2a5d9f", anchor="w")

    def section(col: int, title: str, items: list[tuple]):
        f = tk.Frame(cols)
        f.grid(row=0, column=col, sticky="nw", padx=(0, 16))
        tk.Label(f, text=title, font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(6, 2))
        for text, bat, need, hint in items:
            b = tk.Button(f, text=text, width=30, anchor="w",
                          command=lambda bat=bat, need=need: run_bat(bat, rec_args(need)))
            b.pack(anchor="w", pady=1)
            b.bind("<Enter>", lambda e, h=hint, bat=bat: hint_lbl.config(text=f"{bat}: {h}"))
            b.bind("<Leave>", lambda e: hint_lbl.config(text=""))

    section(0, "Тести", TESTS)
    section(1, "Інструменти", TOOLS)
    hint_lbl.pack(fill="x", padx=8, pady=(2, 0))

    tk.Label(w, text="Звіти, логи, налаштування", font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=8, pady=(8, 2))
    out, wk = output_dir(settings), work_dir(settings)
    links = [
        ("Папка звітів тестів", lambda: open_path(out / "reports")),
        ("Останній звіт", lambda: open_path(newest(out / "reports", ("*.csv", "*.txt")) or out / "reports")),
        ("Debug-картинки", lambda: open_path(out / "debug")),
        ("Логи", lambda: open_path(wk / "logs")),
        ("Останній лог", lambda: open_path(newest(wk / "logs", ("*.log",)) or wk / "logs")),
        ("Останній результат", lambda: open_path(max(out.glob("job_*"), default=out))),
        ("settings.json", lambda: open_path(CONFIG_DIR / "settings.json")),
        ("Профілі ігор", lambda: open_path(PROFILES_DIR)),
        ("Кеш (work)", lambda: open_path(wk)),
        ("Папка інструмента", lambda: open_path(ROOT)),
    ]
    grid = tk.Frame(w)
    grid.pack(fill="x", padx=8)
    for i, (text, fn) in enumerate(links):
        tk.Button(grid, text=text, width=22, command=fn).grid(row=i // 3, column=i % 3, sticky="w", padx=2, pady=1)

    tk.Label(w, text="Стан", font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=8, pady=(8, 2))
    info = tk.Label(w, justify="left", anchor="w", font=("Consolas", 9))
    info.pack(fill="x", padx=8)

    bottom = tk.Frame(w)
    bottom.pack(fill="x", side="bottom", pady=8)
    tk.Button(bottom, text="Оновити", command=refresh).pack(side="left", padx=8)

    def disable():
        if on_disable:
            on_disable()
        w.destroy()

    tk.Button(bottom, text="Вимкнути режим розробника", command=disable).pack(side="right", padx=8)
    tk.Button(bottom, text="Закрити", command=w.destroy).pack(side="right")
    refresh()
    return w
