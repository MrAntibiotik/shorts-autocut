"""Обрізка окремого файлу вебки: на кадрі з камери мишкою обводиться частина, яка піде в шортс.

Рамка тримає пропорції місця під вебку в шортсі (галочка «Пропорції як у шортсі»), тож що обвели —
те й буде у відео. Результат — частки кадру [x, y, w, h] (зберігається в рамках як «webcam_file»).
"""

from __future__ import annotations

import tkinter as tk

from PIL import Image, ImageDraw, ImageTk

MAX_W, MAX_H = 900, 560


def target_aspect(profile: dict) -> float | None:
    """Ширина/висота місця під вебку в шортсі (None — невідомо)."""
    try:
        lay = profile["layout"]
        p = lay[lay["preset"]]
        if lay["preset"] == "split":
            return float(lay["output"]["width"]) / float(p["webcam_height"])
        return float(p["webcam_width"]) / float(p["webcam_height"])
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None


class CamCropDialog:
    """result — [x, y, w, h] у частках кадру, [] — без обрізки (уся камера), None — скасовано."""

    def __init__(self, parent: tk.Misc, img: Image.Image, init: list | None, aspect: float | None):
        self.img, self.aspect = img, aspect
        self.result: list | None = None
        k = min(MAX_W / img.width, MAX_H / img.height, 1.0)
        self.k = k
        self.view = img.resize((max(1, int(img.width * k)), max(1, int(img.height * k))))
        self.w = tk.Toplevel(parent)
        self.w.title("Обрізати вебку")
        self.w.transient(parent)
        tk.Label(self.w, text="Обведіть мишкою частину кадру з вебки, яку показувати в шортсі",
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=10, pady=(10, 2))
        tk.Label(self.w, fg="#555", text="Потягніть від кута до кута. Усе поза рамкою в шортс не потрапить.").pack(
            anchor="w", padx=10)
        self.lock = tk.BooleanVar(value=aspect is not None)
        if aspect:
            tk.Checkbutton(self.w, variable=self.lock, text=f"Пропорції як у шортсі ({aspect:.2f}:1) — "
                           "що обвели, те й буде у відео").pack(anchor="w", padx=10)
        self.cv = tk.Canvas(self.w, width=self.view.width, height=self.view.height, highlightthickness=0,
                            cursor="crosshair")
        self.cv.pack(padx=10, pady=6)
        self.photo = ImageTk.PhotoImage(self.view)
        self.cv.create_image(0, 0, anchor="nw", image=self.photo)
        self.rect = None
        self.shade = []
        self.sel = None
        if init:
            x, y, w, h = init
            self.sel = [x * self.view.width, y * self.view.height, (x + w) * self.view.width,
                        (y + h) * self.view.height]
            self.draw()
        self.cv.bind("<ButtonPress-1>", self.press)
        self.cv.bind("<B1-Motion>", self.drag)
        bt = tk.Frame(self.w)
        bt.pack(fill="x", padx=10, pady=(0, 10))
        tk.Button(bt, text="✓ Зберегти", width=14, bg="#3a7d44", fg="white", font=("Segoe UI", 10, "bold"),
                  command=self.save).pack(side="right")
        tk.Button(bt, text="Скасувати", command=self.w.destroy).pack(side="right", padx=8)
        tk.Button(bt, text="Уся камера (без обрізки)", command=self.full).pack(side="left")
        self.w.grab_set()
        parent.wait_window(self.w)

    def press(self, ev):
        self.start = (ev.x, ev.y)
        self.sel = [ev.x, ev.y, ev.x, ev.y]
        self.draw()

    def drag(self, ev):
        x0, y0 = self.start
        x1 = min(max(ev.x, 0), self.view.width)
        y1 = min(max(ev.y, 0), self.view.height)
        if self.lock.get() and self.aspect:
            w = abs(x1 - x0)
            h = w / self.aspect
            if h > abs(y1 - y0) and abs(y1 - y0) > 0:
                h = abs(y1 - y0)
                w = h * self.aspect
            x1 = x0 + w * (1 if x1 >= x0 else -1)
            y1 = y0 + h * (1 if y1 >= y0 else -1)
            if not (0 <= x1 <= self.view.width and 0 <= y1 <= self.view.height):
                return
        self.sel = [x0, y0, x1, y1]
        self.draw()

    def draw(self):
        for item in [self.rect, *self.shade]:
            if item:
                self.cv.delete(item)
        x0, y0, x1, y1 = self.norm()
        W, H = self.view.width, self.view.height
        opts = {"fill": "black", "stipple": "gray50", "outline": ""}
        self.shade = [self.cv.create_rectangle(0, 0, W, y0, **opts), self.cv.create_rectangle(0, y1, W, H, **opts),
                      self.cv.create_rectangle(0, y0, x0, y1, **opts), self.cv.create_rectangle(x1, y0, W, y1, **opts)]
        self.rect = self.cv.create_rectangle(x0, y0, x1, y1, outline="#ffcc00", width=3)

    def norm(self):
        x0, y0, x1, y1 = self.sel
        return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)

    def save(self):
        if not self.sel:
            return self.full()
        x0, y0, x1, y1 = self.norm()
        if x1 - x0 < 10 or y1 - y0 < 10:
            return
        W, H = self.view.width, self.view.height
        self.result = [round(x0 / W, 4), round(y0 / H, 4), round((x1 - x0) / W, 4), round((y1 - y0) / H, 4)]
        self.w.destroy()

    def full(self):
        self.result = []
        self.w.destroy()


def preview(img: Image.Image, crop: list | None) -> Image.Image:
    """Мініатюра кадру вебки з жовтою рамкою обрізки."""
    im = img.copy()
    if crop:
        d = ImageDraw.Draw(im)
        x, y, w, h = crop
        d.rectangle([x * im.width, y * im.height, (x + w) * im.width, (y + h) * im.height], outline="#ffcc00",
                    width=max(3, im.width // 200))
    return im
