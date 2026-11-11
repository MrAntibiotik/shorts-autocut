"""Редактор субтитрів: перевірити й виправити фрази, потім перерендерити MP4.

Запуск: кнопка «Редагувати субтитри…» у вікні після обробки, у режимі розробника,
або extra\\edit_subs.bat (можна перетягнути на нього папку результату output\\job_…).

Кожна фраза: час у шортсі, текст (можна виправити), ✕ — прибрати фразу, ▶ — прослухати
цей шматок шортсу (якщо поруч із ffmpeg є ffplay). «Зберегти» пише .srt у папці бою;
«Зберегти і рендерити» — ще й робить MP4 заново з виправленими субтитрами.
У DaVinci субтитри — окрема доріжка ST1, там їх так само можна правити прямо на таймлайні.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS.parent))
sys.path.insert(0, str(TOOLS))

from shorts.config import load_settings, output_dir  # noqa: E402
from shorts.subtitles import Cue, read_srt, write_srt  # noqa: E402
from shorts.util import fmt_time  # noqa: E402

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def find_ffplay(settings: dict) -> Path | None:
    try:
        from shorts.media import find_tool
        ff = Path(find_tool(settings, "ffmpeg"))
        p = ff.with_name("ffplay.exe" if os.name == "nt" else "ffplay")
        return p if p.exists() else None
    except Exception:
        return None


class SubsEditor:
    def __init__(self, master: tk.Misc | None, settings: dict, job_dir: Path):
        self.settings = settings
        self.job_dir = job_dir
        self.job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
        self.items = [(rec, sh) for rec in self.job["recordings"] for sh in rec["shorts"]]
        self.ffplay = find_ffplay(settings)
        self.play_proc = None
        self.rows: list[dict] = []
        self.w = tk.Toplevel(master) if master is not None else tk.Tk()
        self.w.title(f"Субтитри — {job_dir.name}")
        self.w.geometry("900x680")
        top = tk.Frame(self.w)
        top.pack(fill="x", padx=10, pady=8)
        tk.Label(top, text="Шортс:", font=("Segoe UI", 10, "bold")).pack(side="left")
        self.combo = ttk.Combobox(top, state="readonly", width=70,
                                  values=[sh["name"] for _, sh in self.items])
        self.combo.pack(side="left", padx=8)
        self.combo.bind("<<ComboboxSelected>>", lambda e: self.load())
        tk.Label(self.w, text="Виправте текст прямо в полях. ✕ — прибрати фразу. ▶ — прослухати цей момент.",
                 fg="#555").pack(anchor="w", padx=10)

        box = tk.Frame(self.w)
        box.pack(fill="both", expand=True, padx=10, pady=6)
        self.canvas = tk.Canvas(box, highlightthickness=0)
        sb = tk.Scrollbar(box, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.canvas.bind_all("<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-e.delta / 120), "units"))

        bottom = tk.Frame(self.w)
        bottom.pack(fill="x", padx=10, pady=8)
        self.b_render = tk.Button(bottom, text="Зберегти і рендерити MP4", font=("Segoe UI", 10, "bold"),
                                  bg="#3a7d44", fg="white", command=self.save_render)
        self.b_render.pack(side="right")
        tk.Button(bottom, text="Зберегти", width=12, command=self.save).pack(side="right", padx=6)
        tk.Button(bottom, text="Відкрити MP4", command=self.open_mp4).pack(side="right")
        self.status = tk.Label(bottom, text="", fg="#2a5d9f", anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        if self.items:
            self.combo.current(0)
            self.load()
        else:
            self.status.config(text="У цьому результаті немає шортсів.")

    # ------------------------------------------------------------------ дані
    def current(self):
        return self.items[self.combo.current()]

    def paths(self):
        from rerender import short_paths
        return short_paths(self.job_dir, self.current()[1])

    def load(self):
        for c in self.inner.winfo_children():
            c.destroy()
        self.rows = []
        srt, _, _ = self.paths()
        cues = read_srt(srt) if srt.exists() else []
        if not cues:
            tk.Label(self.inner, text="Субтитрів у цьому шортсі немає (галочка «Повні фрази + субтитри» "
                     "була вимкнена або ви тут не говорите).", fg="#a05a00", wraplength=820,
                     justify="left").pack(anchor="w", pady=10)
        for i, c in enumerate(cues):
            f = tk.Frame(self.inner)
            f.pack(fill="x", pady=2)
            tk.Label(f, text=f"{fmt_time(c.start, 1)}–{fmt_time(c.end, 1)}", width=14, anchor="w",
                     font=("Consolas", 9)).pack(side="left")
            b = tk.Button(f, text="▶", width=3, command=lambda c=c: self.play(c))
            b.pack(side="left")
            if self.ffplay is None:
                b.config(state="disabled")
            t = tk.Text(f, height=2, width=78, font=("Segoe UI", 10), wrap="word")
            t.insert("1.0", c.text)
            t.pack(side="left", padx=6)
            row = {"cue": c, "text": t, "frame": f, "deleted": False}
            tk.Button(f, text="✕", width=3, fg="#a00", command=lambda r=row: self.toggle(r)).pack(side="left")
            self.rows.append(row)
        self.status.config(text=f"Фраз: {len(cues)}  |  {srt.parent.name}\\{srt.name}")
        self.canvas.yview_moveto(0)

    def toggle(self, r):
        r["deleted"] = not r["deleted"]
        r["text"].config(state="normal", bg="#f3d6d6" if r["deleted"] else "white",
                         fg="#999" if r["deleted"] else "black")

    def edited_cues(self) -> list[Cue]:
        out = []
        for r in self.rows:
            txt = r["text"].get("1.0", "end").strip()
            if r["deleted"] or not txt:
                continue
            out.append(Cue(r["cue"].start, r["cue"].end, txt))
        return out

    # ------------------------------------------------------------------ дії
    def save(self) -> bool:
        if not self.rows and not self.paths()[0].exists():
            return False
        srt = self.paths()[0]
        if srt.exists():
            bak = srt.with_suffix(".srt.orig")
            if not bak.exists():
                bak.write_bytes(srt.read_bytes())       # перша (автоматична) версія — на всяк випадок
        cues = self.edited_cues()
        write_srt(cues, srt)
        self.status.config(text=f"Збережено: {len(cues)} фраз → {srt.name}")
        return True

    def save_render(self):
        if not self.save():
            return
        rec, sh = self.current()
        self.b_render.config(state="disabled")
        self.status.config(text="Рендерю MP4… (зазвичай 10–30 с)")

        def work():
            try:
                from rerender import rerender_short
                mp4 = rerender_short(self.settings, self.job, self.job_dir, rec, sh, log=lambda *_: None)
                self.w.after(0, lambda: self.status.config(text=f"Готово: {mp4.name}"))
            except Exception as e:
                msg = str(e)
                self.w.after(0, lambda: messagebox.showerror("Рендер", msg, parent=self.w))
                self.w.after(0, lambda: self.status.config(text="Помилка рендеру"))
            finally:
                self.w.after(0, lambda: self.b_render.config(state="normal"))

        threading.Thread(target=work, daemon=True).start()

    def open_mp4(self):
        mp4 = self.paths()[2]
        if mp4.exists() and os.name == "nt":
            os.startfile(str(mp4))   # type: ignore[attr-defined]
        elif not mp4.exists():
            messagebox.showinfo("MP4", "MP4 ще не відрендерено — натисніть «Зберегти і рендерити MP4».", parent=self.w)

    def play(self, c: Cue):
        """Прослухати момент фрази в шортсі: шматок оригінального запису (з запасом 0,5 с)."""
        rec, sh = self.current()
        # час у шортсі -> час у записі
        acc, src = 0.0, None
        for s in sh["segments"]:
            d = s["e"] - s["s"]
            if acc <= c.start <= acc + d:
                src = s["s"] + (c.start - acc)
                break
            acc += d
        if src is None:
            return
        if self.play_proc and self.play_proc.poll() is None:
            self.play_proc.kill()
        dur = (c.end - c.start) + 1.0
        self.play_proc = subprocess.Popen(
            [str(self.ffplay), "-hide_banner", "-loglevel", "error", "-nodisp", "-autoexit",
             "-ss", f"{max(0.0, src - 0.5):.2f}", "-t", f"{dur:.2f}", rec["path"]],
            creationflags=_NO_WINDOW)


def latest_job(settings: dict) -> Path | None:
    jobs = sorted(p for p in output_dir(settings).glob("job_*") if (p / "job.json").exists())
    return jobs[-1] if jobs else None


def main() -> int:
    settings = load_settings()
    job_dir = Path(sys.argv[1].strip('"')) if len(sys.argv) > 1 else latest_job(settings)
    if job_dir is None or not (job_dir / "job.json").exists():
        print("Немає результатів обробки (output\\job_…). Спершу зробіть шортси.")
        return 1
    ed = SubsEditor(None, settings, job_dir)
    ed.w.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
