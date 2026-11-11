"""Повзунок часу «як у програвачі»: тягнете — видно час, відпускаєте — показується кадр.
Кнопки ±10 с / ±1 хв і поле для точного часу. Використовується у майстрі рамок і у вікні вибору зон."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from shorts.util import fmt_time, parse_time


class SeekBar(tk.Frame):
    def __init__(self, master, duration: float, t: float, on_seek, **kw):
        super().__init__(master, **kw)
        self.duration = max(1.0, float(duration) - 0.5)   # останні пів секунди кадру може не бути
        self.on_seek = on_seek
        self.var = tk.DoubleVar(value=min(max(0.0, t), self.duration))

        top = tk.Frame(self)
        top.pack(fill="x")
        self.lbl = tk.Label(top, font=("Consolas", 10), width=9, anchor="w")
        self.lbl.pack(side="left")
        self.scale = ttk.Scale(top, from_=0, to=self.duration, orient="horizontal", variable=self.var,
                               command=lambda _v: self._show())
        self.scale.pack(side="left", fill="x", expand=True, padx=6)
        tk.Label(top, text=fmt_time(self.duration, 0), font=("Consolas", 10), fg="#666").pack(side="left")
        # клік у будь-яке місце смуги — одразу туди (а не «крок» як у стандартного повзунка)
        self.scale.bind("<Button-1>", self._jump, add="+")
        self.scale.bind("<ButtonRelease-1>", lambda e: self._commit())

        row = tk.Frame(self)
        row.pack(fill="x", pady=(2, 0))
        for text, d in (("−1 хв", -60), ("−10 с", -10), ("+10 с", 10), ("+1 хв", 60)):
            tk.Button(row, text=text, width=6, command=lambda d=d: self.step(d)).pack(side="left", padx=1)
        tk.Label(row, text="  точно:").pack(side="left")
        self.entry = tk.Entry(row, width=9)
        self.entry.pack(side="left")
        self.entry.bind("<Return>", lambda e: self._from_entry())
        tk.Button(row, text="Показати", command=self._from_entry).pack(side="left", padx=2)
        self.entry.insert(0, fmt_time(self.var.get(), 0))
        self._show()

    def _show(self):
        self.lbl.config(text=fmt_time(self.var.get(), 0))

    def _jump(self, e):
        if "slider" in str(self.scale.identify(e.x, e.y)):
            return None                      # тягнуть повзунок — нехай тягнуть
        w = max(1, self.scale.winfo_width())
        self.var.set(max(0.0, min(self.duration, e.x / w * self.duration)))
        self._show()
        return "break"

    def _commit(self):
        t = float(self.var.get())
        self.entry.delete(0, "end")
        self.entry.insert(0, fmt_time(t, 0))
        self.on_seek(t)

    def step(self, d: float):
        self.var.set(max(0.0, min(self.duration, self.var.get() + d)))
        self._show()
        self._commit()

    def _from_entry(self):
        try:
            t = parse_time(self.entry.get())
        except ValueError:
            return
        if t is None:
            return
        self.var.set(max(0.0, min(self.duration, t)))
        self._show()
        self._commit()

    def get(self) -> float:
        return float(self.var.get())
