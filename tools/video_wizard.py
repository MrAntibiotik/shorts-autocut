"""Майстер для кожного доданого запису: 1) вибір гри (профілю), 2) обов'язкова перевірка рамок
на кадрі саме з цього запису — «Рамки правильні» або «Обвести нові».

Ігри можуть бути різні, а розміщення HUD/вебкамери — інше, тому рамки підтверджуються щоразу.
Готові профілі позначені в json полем "ready": true (зараз — лише World of Tanks);
решта показуються як «в розробці» і не вибираються.
"""

from __future__ import annotations

import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS.parent))
sys.path.insert(0, str(TOOLS))

from shorts.config import load_profile_raw, strip_comments  # noqa: E402
from shorts.errors import UserError  # noqa: E402


PREVIEW_W = 860


def profile_ready(pid: str) -> bool:
    try:
        return bool(strip_comments(load_profile_raw(pid)).get("ready", False))
    except UserError:
        return False


def _raw(pid: str) -> dict:
    return strip_comments(load_profile_raw(pid))


class VideoWizard:
    """Модальне вікно. result — id профілю або None (запис не додається)."""

    def __init__(self, root: tk.Misc, settings: dict, path: str, profiles: list[str], names: dict,
                 state: dict, others_same: callable, remaining: int = 0):
        self.root, self.settings, self.path = root, settings, path
        self.profiles, self.names, self.state = profiles, names, state
        self.others_same = others_same          # (pid) -> скільки інших записів у списку з цим профілем
        self.result: str | None = None
        self.mode: str | None = None            # режим гри (WoT: damage / assist)
        self.remaining = remaining              # скільки ще записів вибрано разом з цим
        self.apply_rest = tk.BooleanVar(value=False)
        self.t = float(state.get("zones_preview_t", 60.0))
        self.photo = None
        self.w = tk.Toplevel(root)
        self.w.title("Новий запис — гра і рамки")
        self.w.transient(root)
        self.w.protocol("WM_DELETE_WINDOW", self.cancel)
        self.body = tk.Frame(self.w)
        self.body.pack(fill="both", expand=True, padx=12, pady=10)
        self.step_profile()
        self.w.update_idletasks()
        x = root.winfo_rootx() + max(0, (root.winfo_width() - self.w.winfo_reqwidth()) // 2)
        y = root.winfo_rooty() + 40
        self.w.geometry(f"+{x}+{y}")
        self.w.grab_set()
        root.wait_window(self.w)

    def clear(self):
        for c in self.body.winfo_children():
            c.destroy()

    def cancel(self):
        self.result = None
        self.w.destroy()

    # ------------------------------------------------------------------ крок 1: гра
    def step_profile(self):
        self.clear()
        b = self.body
        tk.Label(b, text="Крок 1 з 2 — яка гра на цьому записі?", font=("Segoe UI", 13, "bold")).pack(anchor="w")
        tk.Label(b, text=Path(self.path).name, fg="#555").pack(anchor="w", pady=(0, 8))
        last = self.state.get("profile", "wot")
        ready = [p for p in self.profiles if profile_ready(p)]
        self.var = tk.StringVar(value=last if last in ready else (ready[0] if ready else ""))
        for p in sorted(self.profiles, key=lambda p: (p not in ready, self.names.get(p, p))):
            ok = p in ready
            tk.Radiobutton(b, text=self.names.get(p, p) + ("" if ok else "   — в розробці"), value=p,
                           variable=self.var, state="normal" if ok else "disabled",
                           font=("Segoe UI", 11)).pack(anchor="w")
        if not ready:
            tk.Label(b, text="Немає готових профілів.", fg="#a00").pack(anchor="w")
        # режим гри (якщо в профілі є кілька): напр. WoT — «Шкода» / «Засвіт»
        self.mode_box = tk.Frame(b)
        self.mode_box.pack(fill="x", pady=(10, 0))
        self.mode_var = tk.StringVar()
        self.var.trace_add("write", lambda *_: self.show_modes())
        self.show_modes()
        bt = tk.Frame(b)
        bt.pack(fill="x", pady=(12, 0))
        tk.Button(bt, text="Далі →", width=14, font=("Segoe UI", 10, "bold"),
                  command=self.to_zones, state="normal" if ready else "disabled").pack(side="right")
        tk.Button(bt, text="Скасувати", width=12, command=self.cancel).pack(side="right", padx=8)

    def show_modes(self):
        for c in self.mode_box.winfo_children():
            c.destroy()
        pid = self.var.get()
        modes = {k: v for k, v in (_raw(pid).get("modes") or {}).items()} if pid else {}
        if not modes:
            self.mode_var.set("")
            return
        tk.Label(self.mode_box, text="Режим:", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        default = self.state.get(f"mode_{pid}") or _raw(pid).get("default_mode") or next(iter(modes))
        self.mode_var.set(default if default in modes else next(iter(modes)))
        for k, text in modes.items():
            tk.Radiobutton(self.mode_box, text=text, value=k, variable=self.mode_var,
                           font=("Segoe UI", 11)).pack(anchor="w", padx=(16, 0))

    def to_zones(self):
        if not self.var.get():
            return
        self.pid = self.var.get()
        self.step_zones()

    # ------------------------------------------------------------------ крок 2: рамки
    def step_zones(self):
        self.clear()
        b = self.body
        name = self.names.get(self.pid, self.pid)
        tk.Label(b, text=f"Крок 2 з 2 — перевірте рамки ({name})", font=("Segoe UI", 13, "bold")).pack(anchor="w")
        raw = _raw(self.pid)
        note = ("Це збережені рамки. Чи стоять вони на своїх місцях на ЦЬОМУ записі?"
                if raw.get("zones_confirmed") else
                "Рамки ще не обводились (стоять приблизні за замовчуванням) — краще обвести нові.")
        tk.Label(b, text=note, fg="#a05a00" if not raw.get("zones_confirmed") else "#333",
                 wraplength=PREVIEW_W, justify="left").pack(anchor="w", pady=(0, 4))

        self.img_lbl = tk.Label(b, text="Завантажую кадр…", bg="#222", fg="#ddd", width=100, height=20)
        self.img_lbl.pack(pady=(6, 2))
        tk.Label(b, text="Потягніть повзунок до моменту, де видно бій: лічильники, таймер, вебку.",
                 fg="#666").pack(anchor="w")
        from seekbar import SeekBar
        self.seek = SeekBar(b, self.duration(), self.t, self.show_at)
        self.seek.pack(fill="x", pady=(2, 4))
        bt = tk.Frame(b)
        bt.pack(fill="x", pady=(6, 0))
        if self.remaining > 0:
            tk.Checkbutton(b, variable=self.apply_rest, font=("Segoe UI", 10),
                           text=f"Те саме (гра, режим, рамки) для решти вибраних записів — ще {self.remaining}; "
                                f"майстер для них не відкриватиметься").pack(anchor="w", pady=(0, 4), before=bt)
        tk.Button(bt, text="✓ Рамки правильні", width=20, font=("Segoe UI", 10, "bold"), bg="#3a7d44", fg="white",
                  command=self.accept).pack(side="right")
        tk.Button(bt, text="Обвести нові…", width=16, command=self.redraw).pack(side="right", padx=8)
        tk.Button(bt, text="← Інша гра", command=self.step_profile).pack(side="left")
        tk.Button(bt, text="Скасувати", command=self.cancel).pack(side="left", padx=8)
        self.w.after(50, self.reload)

    def duration(self) -> float:
        if not hasattr(self, "_dur"):
            try:
                from shorts.media import probe
                self._dur = probe(self.settings, Path(self.path)).duration
            except Exception:
                self._dur = max(120.0, self.t * 2)
        return self._dur

    def show_at(self, t: float):
        self.t = t
        self.reload()

    def reload(self):
        try:
            from PIL import ImageTk
            from pick_zones import draw_zones, get_frame
            img = draw_zones(get_frame(self.settings, Path(self.path), self.t), _raw(self.pid).get("zones") or {})
            img.thumbnail((PREVIEW_W, PREVIEW_W))
            self.photo = ImageTk.PhotoImage(img)
            self.img_lbl.config(image=self.photo, text="", width=img.width, height=img.height)
        except Exception as e:
            self.img_lbl.config(image="", text=f"Не вдалося показати кадр: {e}", width=100, height=6)

    def redraw(self):
        n = self.others_same(self.pid)
        if n and not messagebox.askyesno(
                "Нові рамки", f"Рамки зберігаються для гри «{self.names.get(self.pid, self.pid)}» і "
                f"застосуються також до {n} інших записів цієї гри у списку. Продовжити?", parent=self.w):
            return
        self.w.grab_release()
        self.w.withdraw()
        self.root.withdraw()
        try:
            subprocess.run([sys.executable, str(TOOLS / "pick_zones.py"), self.path,
                            "--profile", self.pid, "--time", str(int(self.t))])
        finally:
            self.root.deiconify()
            self.w.deiconify()
            self.w.grab_set()
        self.step_zones()      # показуємо нові рамки — і знову просимо підтвердити

    def accept(self):
        self.state["zones_preview_t"] = self.t
        self.state["profile"] = self.pid
        self.mode = self.mode_var.get() or None
        if self.mode:
            self.state[f"mode_{self.pid}"] = self.mode
        self.result = self.pid
        self.w.destroy()
