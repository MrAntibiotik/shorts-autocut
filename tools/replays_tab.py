"""Вкладка «🎮 Реплеї» у вікні автонарізки: скачати реплеї з wotreplays за фільтром → записати їх
через гру й OBS → нарізати шортси (профіль wot_replay_en) → покласти в папку EN-каналу Poster.

Налаштування вкладки зберігаються в work/replays_state.json.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import sys
import threading
import tkinter as tk
import traceback
from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS.parent))

from shorts.config import work_dir  # noqa: E402

DEFAULT_CTA = "Day {day} · Ukrainian veteran on the road to 1000 subs — subscribe 🛡️♥️"


def state_file(settings) -> Path:
    return work_dir(settings) / "replays_state.json"


def load(settings) -> dict:
    try:
        return json.loads(state_file(settings).read_text(encoding="utf-8"))
    except Exception:
        return {}


def save(settings, data: dict) -> None:
    p = state_file(settings)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class _QH(logging.Handler):
    def __init__(self, q: queue.Queue):
        super().__init__(logging.INFO)
        self.q = q
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record):
        self.q.put(("log", self.format(record)))


def build_replays_tab(parent: tk.Misc, settings: dict) -> tk.Frame:
    outer = tk.Frame(parent)
    canvas = tk.Canvas(outer, highlightthickness=0)
    sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    f = tk.Frame(canvas, padx=12, pady=8)
    f.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    win = canvas.create_window((0, 0), window=f, anchor="nw")
    canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
    canvas.configure(yscrollcommand=sb.set)
    canvas.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units")
                    if str(e.widget).startswith(str(outer)) else None)

    st = load(settings)
    replays_default = str(work_dir(settings) / "replays")
    try:
        passed = (date.today() - date.fromisoformat(st.get("day_date", ""))).days
    except ValueError:
        passed = 0

    v = {
        "url": tk.StringVar(value=st.get("url", "")),
        "downloads_dir": tk.StringVar(value=st.get("downloads_dir", str(Path.home() / "Downloads"))),
        "count": tk.IntVar(value=int(st.get("count", 5))),
        "min_damage": tk.IntVar(value=int(st.get("min_damage", 4000))),
        "min_kills": tk.IntVar(value=int(st.get("min_kills", 0))),
        "pause": tk.DoubleVar(value=float(st.get("pause", 3))),
        "game_dir": tk.StringVar(value=st.get("game_dir", r"C:\Games\World_of_Tanks_EU")),
        "replays_dir": tk.StringVar(value=st.get("replays_dir", replays_default)),
        "publish_dir": tk.StringVar(value=st.get("publish_dir", "")),
        "obs_host": tk.StringVar(value=st.get("obs_host", "localhost")),
        "obs_port": tk.IntVar(value=int(st.get("obs_port", 4455))),
        "obs_password": tk.StringVar(value=st.get("obs_password", "")),
        "load_s": tk.IntVar(value=int(st.get("load_s", 60))),
        "cta": tk.StringVar(value=st.get("cta", DEFAULT_CTA)),
        "day": tk.IntVar(value=max(1, int(st.get("day_n", 1)) + max(0, passed))),
        "credit": tk.BooleanVar(value=bool(st.get("credit", True))),
        "do_download": tk.BooleanVar(value=bool(st.get("do_download", True))),
        "do_record": tk.BooleanVar(value=bool(st.get("do_record", True))),
        "do_cut": tk.BooleanVar(value=bool(st.get("do_cut", True))),
    }

    def persist():
        data = {}
        for k, var in v.items():
            try:
                data[k] = var.get()
            except tk.TclError:
                pass
        data["day_n"] = data.pop("day", 1)
        data["day_date"] = date.today().isoformat()
        save(settings, data)

    tk.Label(f, text="Реплеї → англомовні шортси", font=("Segoe UI", 15, "bold")).pack(anchor="w")
    tk.Label(f, text="Скачує реплеї з wotreplays за вашим фільтром, відтворює їх у грі (англійський клієнт) і записує "
                     "через OBS, нарізає шортси й кладе їх у папку EN-каналу Poster — далі публікація за розкладом.",
             fg="#555", wraplength=720, justify="left").pack(anchor="w", pady=(0, 6))

    def row(box, label, var, width=48, browse=None, show=None):
        r = tk.Frame(box)
        r.pack(fill="x", pady=2)
        tk.Label(r, text=label, width=24, anchor="w").pack(side="left")
        e = tk.Entry(r, textvariable=var, width=width, show=show or "")
        e.pack(side="left", fill="x", expand=True)
        if browse:
            tk.Button(r, text="…", width=3, command=browse).pack(side="left", padx=(4, 0))
        return e

    def pick_dir(var):
        def go():
            d = filedialog.askdirectory(initialdir=var.get() or os.getcwd())
            if d:
                var.set(d)
                refresh_version()
        return go

    # ------------------------------------------------------------------ 1. скачування
    b1 = tk.LabelFrame(f, text=" 1. Звідки реплеї ", padx=8, pady=6)
    b1.pack(fill="x", pady=4)
    row(b1, "Забирати з папки", v["downloads_dir"], browse=pick_dir(v["downloads_dir"]))
    tk.Label(b1, text="Відкрийте сайт реплеїв (напр. wot-record.com) з фільтром і тисніть «Download» на боях, що сподобались, — "
                      "підхожі .wotreplay звідси перенесуться в папку реплеїв (непідхожі лишаться на місці).",
             fg="#666", font=("Segoe UI", 8), wraplength=700, justify="left").pack(anchor="w")
    row(b1, "або посилання з фільтром", v["url"], width=60)
    tk.Label(b1, text="Автоскачування — лише з сайтів, що дозволяють його у robots.txt (wot-record.com — забороняє). "
                      "Порожньо — лише папка вище.",
             fg="#666", font=("Segoe UI", 8), wraplength=700, justify="left").pack(anchor="w")
    r = tk.Frame(b1)
    r.pack(fill="x", pady=2)
    for label, key, lo, hi in (("Скільки нових", "count", 1, 100), ("Шкода від", "min_damage", 0, 20000),
                               ("Фрагів від", "min_kills", 0, 15)):
        tk.Label(r, text=label).pack(side="left")
        tk.Spinbox(r, from_=lo, to=hi, width=6, textvariable=v[key]).pack(side="left", padx=(2, 12))
    tk.Label(r, text="Пауза, с").pack(side="left")
    tk.Spinbox(r, from_=1, to=30, width=4, textvariable=v["pause"]).pack(side="left", padx=2)
    row(b1, "Папка реплеїв", v["replays_dir"], browse=pick_dir(v["replays_dir"]))

    # ------------------------------------------------------------------ 2. запис
    b2 = tk.LabelFrame(f, text=" 2. Запис: гра + OBS ", padx=8, pady=6)
    b2.pack(fill="x", pady=4)
    row(b2, "Папка гри", v["game_dir"], browse=pick_dir(v["game_dir"]))
    lbl_ver = tk.Label(b2, anchor="w", font=("Segoe UI", 9))
    lbl_ver.pack(anchor="w")
    r = tk.Frame(b2)
    r.pack(fill="x", pady=2)
    tk.Label(r, text="OBS WebSocket:").pack(side="left")
    tk.Entry(r, textvariable=v["obs_host"], width=12).pack(side="left", padx=2)
    tk.Label(r, text="порт").pack(side="left")
    tk.Entry(r, textvariable=v["obs_port"], width=6).pack(side="left", padx=2)
    tk.Label(r, text="пароль").pack(side="left")
    tk.Entry(r, textvariable=v["obs_password"], width=16, show="•").pack(side="left", padx=2)
    tk.Button(r, text="Перевірити OBS", command=lambda: check_obs()).pack(side="left", padx=6)
    r = tk.Frame(b2)
    r.pack(fill="x", pady=2)
    tk.Label(r, text="Завантаження гри, с").pack(side="left")
    tk.Spinbox(r, from_=20, to=300, width=5, textvariable=v["load_s"]).pack(side="left", padx=2)
    tk.Label(b2, text="Гра — англійською, голос екіпажу стандартний, музика вимкнена. OBS: сцена з захопленням гри, "
                      "Інструменти → Налаштування WebSocket-сервера → увімкнути.",
             fg="#666", font=("Segoe UI", 8), wraplength=700, justify="left").pack(anchor="w")

    # ------------------------------------------------------------------ 3. нарізка й публікація
    b3 = tk.LabelFrame(f, text=" 3. Нарізка й передача в Poster (EN-канал) ", padx=8, pady=6)
    b3.pack(fill="x", pady=4)
    row(b3, "Папка шортсів EN-каналу", v["publish_dir"], browse=pick_dir(v["publish_dir"]))
    tk.Label(b3, text="Та сама папка, що в Poster → 🇬🇧 EN-канал → «Папка шортсів». Порожньо — шортси лише в output.",
             fg="#666", font=("Segoe UI", 8)).pack(anchor="w")
    row(b3, "Заклик у кінці шортсу", v["cta"], width=60)
    r = tk.Frame(b3)
    r.pack(fill="x", pady=2)
    tk.Label(r, text="{day} =").pack(side="left")
    tk.Spinbox(r, from_=1, to=9999, width=5, textvariable=v["day"]).pack(side="left", padx=2)
    tk.Label(r, text="(росте щодня сам)", fg="#666").pack(side="left")
    tk.Checkbutton(b3, text="Вказувати автора реплею в описі («Replay by <нік> via wotreplays»)",
                   variable=v["credit"]).pack(anchor="w")

    # ------------------------------------------------------------------ запуск
    b4 = tk.Frame(f)
    b4.pack(fill="x", pady=(8, 2))
    for text, key in (("Забрати реплеї", "do_download"), ("Записати", "do_record"), ("Нарізати", "do_cut")):
        tk.Checkbutton(b4, text=text, variable=v[key]).pack(side="left")
    btn_go = tk.Button(b4, text="▶  Почати", font=("Segoe UI", 10, "bold"), bg="#2e7d32", fg="white", width=14)
    btn_go.pack(side="right")
    btn_stop = tk.Button(b4, text="⏹ Зупинити", state="disabled")
    btn_stop.pack(side="right", padx=6)
    tk.Button(b4, text="📂 Реплеї", command=lambda: open_dir(v["replays_dir"].get())).pack(side="right")

    pbar = ttk.Progressbar(f, mode="determinate", maximum=1000)
    pbar.pack(fill="x", pady=(6, 2))
    lbl_stage = tk.Label(f, anchor="w", font=("Segoe UI", 9))
    lbl_stage.pack(fill="x")
    logbox = tk.Text(f, height=12, font=("Consolas", 9), wrap="word")
    logbox.pack(fill="both", expand=True, pady=(4, 0))

    q: queue.Queue = queue.Queue()
    cancel = threading.Event()
    running = {"on": False}

    def append(text):
        logbox.insert("end", text + "\n")
        logbox.see("end")

    def open_dir(path):
        Path(path).mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(path)  # noqa: S606

    def refresh_version():
        from shorts.replay_record import client_version
        ver = client_version(Path(v["game_dir"].get()))
        lbl_ver.config(text=(f"Версія гри: {'.'.join(map(str, ver))} — скачуватимуться лише реплеї цієї версії"
                             if ver else "⚠️ version.xml не знайдено — перевірте папку гри (версію реплеїв не перевірятиму)"),
                       fg="#2e7d32" if ver else "#a05a00")

    def check_obs():
        from shorts.replay_record import Obs, RecordError
        try:
            o = Obs(v["obs_host"].get(), int(v["obs_port"].get()), v["obs_password"].get())
            messagebox.showinfo("OBS", "✅ Зв'язок з OBS є. Зараз " + ("йде запис" if o.recording() else "запис не йде"))
        except (RecordError, tk.TclError, ValueError) as e:
            messagebox.showerror("OBS", str(e))

    def work(params: dict):
        handler = _QH(q)
        loggers = [logging.getLogger(n) for n in ("replays", "pipeline", "vision", "selection")]
        for lg in loggers:
            lg.addHandler(handler)
            lg.setLevel(logging.INFO)
        try:
            from shorts.replay_download import DownloadOptions, fetch
            from shorts.replay_job import ReplayJobOptions, run
            from shorts.replay_record import RecordOptions, client_version
            game_dir = Path(params["game_dir"])
            ver = client_version(game_dir)
            if params["do_download"]:
                from shorts.replay_download import Blocked, ImportOptions, import_folder
                if not params["url"].strip() and not params["downloads_dir"].strip():
                    raise ValueError("Вкажіть папку завантажень або посилання з фільтром (або зніміть галочку «Забрати реплеї»)")
                if params["downloads_dir"].strip():
                    res = import_folder(ImportOptions(src=Path(params["downloads_dir"]), dest=Path(params["replays_dir"]),
                                                      client_version=ver[:3], min_damage=int(params["min_damage"]),
                                                      min_kills=int(params["min_kills"])))
                    q.put(("log", f"З папки забрано: {len(res.saved)}; лишилось: "
                                  + (", ".join(f"{k} — {n}" for k, n in res.skipped.items()) or "0")))
            if params["do_download"] and params["url"].strip():
                try:
                    res = fetch(DownloadOptions(url=params["url"].strip(), dest=Path(params["replays_dir"]),
                                            count=int(params["count"]), client_version=ver[:3],
                                            min_damage=int(params["min_damage"]), min_kills=int(params["min_kills"]),
                                            pause_s=float(params["pause"])),
                            progress=lambda t, fr: q.put(("progress", ("Скачування: " + t, fr * 0.1))),
                            cancel=cancel.is_set)
                    q.put(("log", f"Скачано: {len(res.saved)}; пропущено: "
                                  + (", ".join(f"{k} — {n}" for k, n in res.skipped.items()) or "0")))
                    for e in res.errors:
                        q.put(("log", "⚠️ " + e))
                except Blocked as e:
                    q.put(("log", "⛔ " + str(e)))
            if params["do_record"] or params["do_cut"]:
                ropts = RecordOptions(game_dir=game_dir, obs_host=params["obs_host"], obs_port=int(params["obs_port"]),
                                      obs_password=params["obs_password"], load_s=float(params["load_s"]))
                jopts = ReplayJobOptions(replays_dir=Path(params["replays_dir"]),
                                         publish_dir=Path(params["publish_dir"]) if params["publish_dir"].strip() else None,
                                         cta_text=params["cta_text"], credit_author=bool(params["credit"]),
                                         record=bool(params["do_record"]), cut=bool(params["do_cut"]))
                summary = run(settings, jopts, ropts,
                              progress=lambda t, fr: q.put(("progress", (t, 0.1 + 0.9 * fr))), cancel=cancel.is_set)
                q.put(("log", f"Готово. Реплеїв: {summary['replays']}, записано: {summary['recorded']}, "
                              f"шортсів: {len(summary['shorts'])}, у Poster: {len(summary['published'])}"))
                for e in summary["errors"]:
                    q.put(("log", "❌ " + e))
            q.put(("done", None))
        except Exception as e:  # noqa: BLE001
            if type(e).__name__ == "Cancelled":
                q.put(("done", "Зупинено"))
            else:
                logging.getLogger("replays").error("%s", traceback.format_exc())
                q.put(("error", f"{type(e).__name__}: {e}"))
        finally:
            for lg in loggers:
                lg.removeHandler(handler)

    def poll():
        try:
            while True:
                kind, data = q.get_nowait()
                if kind == "log":
                    append(data)
                elif kind == "progress":
                    text, fr = data
                    lbl_stage.config(text=text)
                    pbar["value"] = int(1000 * max(0.0, min(1.0, fr)))
                elif kind in ("done", "error"):
                    running["on"] = False
                    btn_go.config(state="normal")
                    btn_stop.config(state="disabled")
                    if kind == "error":
                        append("❌ " + data)
                        messagebox.showerror("Реплеї", data)
                    else:
                        lbl_stage.config(text=data or "Готово")
                        pbar["value"] = 1000 if not data else pbar["value"]
        except queue.Empty:
            pass
        f.after(300, poll)

    def start():
        if running["on"]:
            return
        persist()
        params = {}
        for k, var in v.items():
            try:
                params[k] = var.get()
            except tk.TclError:
                messagebox.showerror("Реплеї", f"Неправильне значення в полі «{k}»")
                return
        params["cta_text"] = params["cta"].replace("{day}", str(params["day"])).replace("{день}", str(params["day"]))
        if not (params["do_download"] or params["do_record"] or params["do_cut"]):
            messagebox.showinfo("Реплеї", "Виберіть хоча б одну дію: скачати, записати або нарізати.")
            return
        if params["do_record"] and not messagebox.askokcancel(
                "Запис реплеїв", "Під час запису гра буде на весь екран, ПК краще не чіпати.\n"
                                 "OBS має бути відкритий, сцена — із захопленням гри. Почати?"):
            return
        cancel.clear()
        running["on"] = True
        btn_go.config(state="disabled")
        btn_stop.config(state="normal")
        logbox.delete("1.0", "end")
        threading.Thread(target=work, args=(params,), daemon=True, name="replays").start()

    def stop():
        cancel.set()
        lbl_stage.config(text="Зупиняю… (поточний крок завершиться)")

    btn_go.config(command=start)
    btn_stop.config(command=stop)
    refresh_version()
    poll()
    return outer


__all__ = ["build_replays_tab"]
