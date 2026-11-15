"""Вікно прогресу довгої дії (хайлайт з бота тощо): етап, смуга, час, «Відкрити папку» в кінці.

Робота йде у фоновому потоці, вікно — у головному (tkinter інакше не можна).
    run_with_window("Хайлайт — бій 5", work)   # work(report) -> dict; report(текст, частка або None)
"""

from __future__ import annotations

import os
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk


def _fmt(sec: float) -> str:
    sec = int(max(0, sec))
    return f"{sec // 60}:{sec % 60:02d}"


def run_with_window(title: str, work, folder_of=None, close_after_s: int = 30):
    """Показує вікно, поки виконується work(report). Повертає результат work або піднімає його виняток.
    folder_of(result) -> Path — що відкрити кнопкою «📂 Відкрити папку»."""
    q: queue.Queue = queue.Queue()
    box: dict = {}

    def report(text: str, frac=None):
        q.put(("p", text, frac))

    def runner():
        try:
            box["result"] = work(report)
            q.put(("done", None, None))
        except BaseException as e:  # noqa: BLE001
            box["error"] = e
            q.put(("error", str(e), None))

    root = tk.Tk()
    root.title(title)
    root.resizable(False, False)
    try:
        root.attributes("-topmost", True)
        root.after(1500, lambda: root.attributes("-topmost", False))
    except tk.TclError:
        pass
    pad = {"padx": 14, "pady": 4}
    tk.Label(root, text=title, font=("Segoe UI", 12, "bold"), anchor="w").pack(fill="x", padx=14, pady=(12, 2))
    stage = tk.Label(root, text="Починаю…", font=("Segoe UI", 10), anchor="w", justify="left", wraplength=480)
    stage.pack(fill="x", **pad)
    bar = ttk.Progressbar(root, length=480, mode="indeterminate", maximum=1000)
    bar.pack(**pad)
    bar.start(12)
    info = tk.Label(root, text="", fg="#555", font=("Segoe UI", 9), anchor="w")
    info.pack(fill="x", **pad)
    btns = tk.Frame(root)
    btns.pack(fill="x", padx=14, pady=(4, 12))
    t0 = time.time()
    st = {"frac": None, "since": t0, "done": False, "left": close_after_s}

    def set_frac(frac):
        if frac is None:
            if str(bar.cget("mode")) != "indeterminate":
                bar.config(mode="indeterminate")
                bar.start(12)
            st["frac"] = None
            return
        if str(bar.cget("mode")) != "determinate":
            bar.stop()
            bar.config(mode="determinate")
            st["since"] = time.time()
        bar["value"] = int(frac * 1000)
        st["frac"] = frac

    def finish(ok: bool, text: str):
        st["done"] = True
        bar.stop()
        bar.config(mode="determinate")
        bar["value"] = 1000 if ok else 0
        stage.config(text=text, fg="#2e7d32" if ok else "#b00020")
        folder = None
        if ok and folder_of:
            try:
                folder = folder_of(box.get("result"))
            except Exception:  # noqa: BLE001
                folder = None
        if folder and os.name == "nt":
            tk.Button(btns, text="📂 Відкрити папку", command=lambda: os.startfile(str(folder))).pack(side="left")  # type: ignore[attr-defined]
        tk.Button(btns, text="Закрити", width=12, command=root.destroy).pack(side="right")

        def countdown():
            if not root.winfo_exists():
                return
            st["left"] -= 1
            if st["left"] <= 0:
                root.destroy()
                return
            info.config(text=f"Минуло {_fmt(time.time() - t0)} · вікно закриється за {st['left']} с")
            root.after(1000, countdown)
        if not ok:
            st["left"] = min(st["left"], 20)   # бот чекає на це вікно — довго не тримаємо
        countdown()

    def poll():
        try:
            while True:
                kind, text, frac = q.get_nowait()
                if kind == "p":
                    pct = f"  {int(frac * 100)}%" if frac is not None else ""
                    stage.config(text=f"{text}{pct}")
                    set_frac(frac)
                elif kind == "done":
                    finish(True, "✅ Готово")
                elif kind == "error":
                    finish(False, f"❌ Не вийшло: {text[-400:]}")
        except queue.Empty:
            pass
        if not st["done"]:
            el = time.time() - t0
            eta = ""
            f = st["frac"]
            if f and f > 0.03:
                left = (time.time() - st["since"]) * (1 - f) / f
                eta = f" · залишилось ≈ {_fmt(left)}"
            info.config(text=f"Минуло {_fmt(el)}{eta}")
        root.after(300, poll)

    threading.Thread(target=runner, daemon=True).start()
    root.after(100, poll)
    root.mainloop()
    if not box.get("result") and not box.get("error"):
        # вікно закрили вручну до кінця — чекаємо роботу (вона йде далі)
        while "result" not in box and "error" not in box:
            time.sleep(0.5)
    if "error" in box:
        raise box["error"]
    return box["result"]


if __name__ == "__main__":   # демонстрація
    def demo(report):
        report("Шукаю запис стріму…")
        time.sleep(1)
        for i in range(101):
            report("Рендер хайлайту (7:30) у якості оригіналу…", i / 100)
            time.sleep(0.03)
        report("Opener: збираю 5 найяскравіших моментів у шаблон…")
        time.sleep(1.5)
        return {"mp4": str(Path.home())}
    print(run_with_window("Хайлайт — бій 5", demo, folder_of=lambda r: Path(r["mp4"])))
