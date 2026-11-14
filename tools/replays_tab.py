"""Вкладка «🎮 Реплеї» у вікні автонарізки: фільтр сайту wot-record.com → ви тиснете «Download» у браузері →
програма сама забирає реплеї з «Завантажень», записує їх через гру й OBS, нарізає шортси (профіль wot_replay_en)
і кладе в папку EN-каналу Poster. Працює безперервно, доки не натиснете «Зупинити».

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
import time
import traceback
import webbrowser
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
        "sort": tk.StringVar(value=st.get("sort", "date")),
        "version": tk.StringVar(value=st.get("version", "")),
        "tank_search": tk.StringVar(value=""),
        "open_on_start": tk.BooleanVar(value=bool(st.get("open_on_start", True))),
        "idle_min": tk.IntVar(value=int(st.get("idle_min", 3))),
    }
    sel_saved = st.get("wr_sel") or {}
    sel_vars: dict[str, dict[str, tk.BooleanVar]] = {}      # нація/рівень/тип/майстерність/режим -> {value: var}
    lists: dict[str, tk.Listbox] = {}                         # карта, танк
    tank_rows: list[dict] = []                                # танки, показані в списку зараз
    chosen_tanks: set[str] = set(sel_saved.get("tank") or [])

    def persist():
        data = {}
        for k, var in v.items():
            try:
                data[k] = var.get()
            except tk.TclError:
                pass
        data["day_n"] = data.pop("day", 1)
        data["day_date"] = date.today().isoformat()
        data.pop("tank_search", None)
        data["wr_sel"] = selection()
        save(settings, data)

    tk.Label(f, text="Реплеї → англомовні шортси", font=("Segoe UI", 15, "bold")).pack(anchor="w")
    tk.Label(f, text="Виберіть фільтр → «▶ Почати» відкриє підбірку на wot-record.com → тисніть «Download» на боях, що "
                     "сподобались. Далі все саме: реплеї забираються з «Завантажень», записуються через гру й OBS, "
                     "нарізаються й ідуть у Poster EN-каналу. Програма працює, доки не натиснете «Зупинити».",
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

    # ------------------------------------------------------------------ 1. підбірка (фільтр сайту)
    from shorts.replay_filter import build_url, load_filters, tanks_for
    try:
        FL = load_filters()
    except Exception as e:  # noqa: BLE001
        FL = {}
        logging.getLogger("replays").warning("Фільтри wot-record недоступні: %s", e)

    b1 = tk.LabelFrame(f, text=" 1. Підбірка реплеїв (фільтр wot-record.com) ", padx=8, pady=6)
    b1.pack(fill="x", pady=4)

    def checks(box, key, title, cols=6):
        fr = tk.Frame(box)
        fr.pack(fill="x", pady=1)
        tk.Label(fr, text=title, width=12, anchor="w").grid(row=0, column=0, sticky="nw")
        sel_vars[key] = {}
        for n, o in enumerate(FL.get(key, [])):
            var = tk.BooleanVar(value=o["value"] in (sel_saved.get(key) or []))
            sel_vars[key][o["value"]] = var
            tk.Checkbutton(fr, text=o["label"], variable=var, command=on_filter_change).grid(
                row=n // cols, column=1 + n % cols, sticky="w")

    def selection() -> dict:
        out = {k: [val for val, var in d.items() if var.get()] for k, d in sel_vars.items()}
        if "map" in lists:
            out["map"] = [FL["map"][i]["value"] for i in lists["map"].curselection()]
        out["tank"] = sorted(chosen_tanks)
        ver = v["version"].get().strip()
        out["version"] = [ver] if ver else []
        return out

    def current_url() -> str:
        return build_url(selection(), sort=v["sort"].get() or "date") if FL else ""

    def refresh_tanks(*_):
        if "tank" not in lists:
            return
        lb = lists["tank"]
        sel = {k: [val for val, var in d.items() if var.get()] for k, d in sel_vars.items()}
        tank_rows[:] = tanks_for(sel.get("nation", []), sel.get("tier", []), sel.get("type", []),
                                 v["tank_search"].get(), FL)
        lb.delete(0, "end")
        for i, t in enumerate(tank_rows):
            lb.insert("end", t["label"])
            if t["value"] in chosen_tanks:
                lb.selection_set(i)
        lbl_tanks.config(text=f"танків у списку: {len(tank_rows)}, вибрано: {len(chosen_tanks)}")

    def on_tank_select(_=None):
        lb = lists["tank"]
        shown = {t["value"] for t in tank_rows}
        picked = {tank_rows[i]["value"] for i in lb.curselection()}
        chosen_tanks.difference_update(shown - picked)
        chosen_tanks.update(picked)
        lbl_tanks.config(text=f"танків у списку: {len(tank_rows)}, вибрано: {len(chosen_tanks)}")
        on_filter_change(tanks=False)

    def on_filter_change(tanks=True):
        if tanks:
            refresh_tanks()
        lbl_url.config(text=current_url() or "файл фільтрів config/wot_record_filters.json не знайдено")

    for key, title, cols in (("nation", "Нація", 6), ("tier", "Рівень", 11), ("type", "Тип", 5),
                             ("mastery", "Майстерність", 4), ("game", "Режим", 6)):
        checks(b1, key, title, cols)

    r = tk.Frame(b1)
    r.pack(fill="x", pady=2)
    tk.Label(r, text="Версія", width=12, anchor="w").pack(side="left")
    ttk.Combobox(r, textvariable=v["version"], width=8,
                 values=[""] + [o["value"] for o in FL.get("version", [])]).pack(side="left")
    tk.Label(r, text="(порожньо — будь-яка; ставиться за вашою грою)", fg="#666").pack(side="left", padx=4)
    tk.Label(r, text="Сортування").pack(side="left", padx=(16, 2))
    sorts = FL.get("sorts", [{"value": "date", "label": "Нові"}])
    cb_sort = ttk.Combobox(r, width=12, state="readonly", values=[o["label"] for o in sorts])
    cb_sort.pack(side="left")
    cb_sort.current(next((i for i, o in enumerate(sorts) if o["value"] == v["sort"].get()), 0))

    def on_sort(_=None):
        v["sort"].set(sorts[cb_sort.current()]["value"])
        on_filter_change(tanks=False)
    cb_sort.bind("<<ComboboxSelected>>", on_sort)
    v["version"].trace_add("write", lambda *_: on_filter_change(tanks=False))

    lr = tk.Frame(b1)
    lr.pack(fill="x", pady=2)
    mf = tk.Frame(lr)
    mf.pack(side="left", fill="y")
    tk.Label(mf, text="Карти (Ctrl — кілька)").pack(anchor="w")
    lists["map"] = tk.Listbox(mf, selectmode="extended", height=8, width=24, exportselection=False)
    for i, o in enumerate(FL.get("map", [])):
        lists["map"].insert("end", o["label"])
        if o["value"] in (sel_saved.get("map") or []):
            lists["map"].selection_set(i)
    lists["map"].pack(side="left", fill="y")
    msb = ttk.Scrollbar(mf, command=lists["map"].yview)
    msb.pack(side="left", fill="y")
    lists["map"].config(yscrollcommand=msb.set)
    lists["map"].bind("<<ListboxSelect>>", lambda e: on_filter_change(tanks=False))

    tf = tk.Frame(lr)
    tf.pack(side="left", fill="both", expand=True, padx=(12, 0))
    tr = tk.Frame(tf)
    tr.pack(fill="x")
    tk.Label(tr, text="Танки — пошук:").pack(side="left")
    tk.Entry(tr, textvariable=v["tank_search"], width=16).pack(side="left", padx=2)
    tk.Button(tr, text="Скинути вибір", command=lambda: (chosen_tanks.clear(), on_filter_change())).pack(side="left")
    lbl_tanks = tk.Label(tf, anchor="w", fg="#666", font=("Segoe UI", 8))
    lbl_tanks.pack(fill="x")
    lists["tank"] = tk.Listbox(tf, selectmode="multiple", height=7, exportselection=False)
    lists["tank"].pack(side="left", fill="both", expand=True)
    tsb = ttk.Scrollbar(tf, command=lists["tank"].yview)
    tsb.pack(side="left", fill="y")
    lists["tank"].config(yscrollcommand=tsb.set)
    lists["tank"].bind("<<ListboxSelect>>", on_tank_select)
    v["tank_search"].trace_add("write", refresh_tanks)

    ur = tk.Frame(b1)
    ur.pack(fill="x", pady=(4, 0))
    tk.Button(ur, text="🌐 Відкрити підбірку", command=lambda: open_selection()).pack(side="left")
    tk.Checkbutton(ur, text="відкривати при «▶ Почати»", variable=v["open_on_start"]).pack(side="left", padx=6)
    lbl_url = tk.Label(b1, anchor="w", fg="#1565c0", font=("Segoe UI", 8), wraplength=720, justify="left")
    lbl_url.pack(fill="x")

    b1b = tk.LabelFrame(f, text=" Що забирати з «Завантажень» ", padx=8, pady=6)
    b1b.pack(fill="x", pady=4)
    row(b1b, "Папка «Завантаження»", v["downloads_dir"], browse=pick_dir(v["downloads_dir"]))
    r = tk.Frame(b1b)
    r.pack(fill="x", pady=2)
    for label, key, lo, hi in (("Шкода від", "min_damage", 0, 20000), ("Фрагів від", "min_kills", 0, 15)):
        tk.Label(r, text=label).pack(side="left")
        tk.Spinbox(r, from_=lo, to=hi, width=6, textvariable=v[key]).pack(side="left", padx=(2, 12))
    tk.Label(r, text="Почати запис, коли нових немає").pack(side="left")
    tk.Spinbox(r, from_=0, to=60, width=4, textvariable=v["idle_min"]).pack(side="left", padx=2)
    tk.Label(r, text="хв (0 — лише кнопкою)").pack(side="left")
    tk.Label(b1b, text="Реплеї іншої версії гри, недограні або слабші за пороги лишаються в «Завантаженнях».",
             fg="#666", font=("Segoe UI", 8)).pack(anchor="w")
    row(b1b, "Папка реплеїв", v["replays_dir"], browse=pick_dir(v["replays_dir"]))
    row(b1b, "Інший сайт (посилання)", v["url"], width=60)
    tk.Label(b1b, text="Необов'язково: автоскачування за посиланням з фільтром — лише з сайтів, що дозволяють його "
                       "у robots.txt (wot-record.com забороняє).",
             fg="#666", font=("Segoe UI", 8), wraplength=700, justify="left").pack(anchor="w")

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
    tk.Checkbutton(b3, text="Вказувати автора реплею в описі («Replay by <нік>»)",
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
    btn_now = tk.Button(b4, text="🎬 Записати зібрані", state="disabled", command=lambda: go_now.set())
    btn_now.pack(side="right")
    tk.Button(b4, text="📂 Реплеї", command=lambda: open_dir(v["replays_dir"].get())).pack(side="right")

    pbar = ttk.Progressbar(f, mode="determinate", maximum=1000)
    pbar.pack(fill="x", pady=(6, 2))
    lbl_stage = tk.Label(f, anchor="w", font=("Segoe UI", 9))
    lbl_stage.pack(fill="x")
    logbox = tk.Text(f, height=12, font=("Consolas", 9), wrap="word")
    logbox.pack(fill="both", expand=True, pady=(4, 0))

    q: queue.Queue = queue.Queue()
    cancel = threading.Event()
    go_now = threading.Event()
    running = {"on": False}

    def append(text):
        logbox.insert("end", text + "\n")
        logbox.see("end")

    def open_dir(path):
        Path(path).mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(path)  # noqa: S606

    def open_selection():
        url = current_url()
        if url:
            webbrowser.open(url)

    def refresh_version():
        from shorts.replay_record import client_version
        from shorts.replay_filter import version_value
        ver = client_version(Path(v["game_dir"].get()))
        if ver and not v["version"].get().strip():
            v["version"].set(version_value(ver))
        lbl_ver.config(text=(f"Версія гри: {'.'.join(map(str, ver))} — скачуватимуться лише реплеї цієї версії"
                             if ver else "⚠️ version.xml не знайдено — перевірте папку гри (версію реплеїв не перевірятиму)"),
                       fg="#2e7d32" if ver else "#a05a00")
        on_filter_change(tanks=False)

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
            from shorts.replay_download import Blocked, ImportOptions, import_folder
            from shorts.replay_job import pending_replays
            game_dir = Path(params["game_dir"])
            ver = client_version(game_dir)
            replays_dir = Path(params["replays_dir"])
            if params["do_download"] and params["url"].strip():
                try:
                    res = fetch(DownloadOptions(url=params["url"].strip(), dest=replays_dir,
                                                count=int(params["count"]), client_version=ver[:3],
                                                min_damage=int(params["min_damage"]), min_kills=int(params["min_kills"]),
                                                pause_s=float(params["pause"])),
                                progress=lambda t, fr: q.put(("progress", ("Скачування: " + t, fr))),
                                cancel=cancel.is_set)
                    q.put(("log", f"Скачано: {len(res.saved)}; пропущено: "
                                  + (", ".join(f"{k} — {n}" for k, n in res.skipped.items()) or "0")))
                    for e in res.errors:
                        q.put(("log", "⚠️ " + e))
                except Blocked as e:
                    q.put(("log", "⛔ " + str(e)))
            watch = bool(params["do_download"] and params["downloads_dir"].strip())
            idle_s = max(0, int(params["idle_min"])) * 60
            last_new = time.time()
            seen: set = set()                 # непідхожі файли в «Завантаженнях» — перевіряються один раз
            ropts = RecordOptions(game_dir=game_dir, obs_host=params["obs_host"], obs_port=int(params["obs_port"]),
                                  obs_password=params["obs_password"], load_s=float(params["load_s"]))
            jopts = ReplayJobOptions(replays_dir=replays_dir,
                                     publish_dir=Path(params["publish_dir"]) if params["publish_dir"].strip() else None,
                                     cta_text=params["cta_text"], credit_author=bool(params["credit"]),
                                     record=bool(params["do_record"]), cut=bool(params["do_cut"]))
            total = {"imported": 0, "recorded": 0, "shorts": 0, "published": 0}
            while not cancel.is_set():
                if watch:
                    res = import_folder(ImportOptions(src=Path(params["downloads_dir"]), dest=replays_dir,
                                                      client_version=ver[:3], min_damage=int(params["min_damage"]),
                                                      min_kills=int(params["min_kills"]), min_age_s=3, seen=seen))
                    if res.saved:
                        last_new = time.time()
                        total["imported"] += len(res.saved)
                pend = pending_replays(replays_dir) if (params["do_record"] or params["do_cut"]) else []
                idle = time.time() - last_new
                due = pend and (go_now.is_set() or not watch or (idle_s and idle >= idle_s))
                if due:
                    go_now.clear()
                    q.put(("log", f"▶ Записую й нарізаю: {len(pend)} реплеїв"))
                    summary = run(settings, jopts, ropts,
                                  progress=lambda t, fr: q.put(("progress", (t, fr))), cancel=cancel.is_set)
                    total["recorded"] += summary["recorded"]
                    total["shorts"] += len(summary["shorts"])
                    total["published"] += len(summary["published"])
                    for e in summary["errors"]:
                        q.put(("log", "❌ " + e))
                    q.put(("log", f"✓ Разом: забрано {total['imported']}, записано {total['recorded']}, "
                                  f"шортсів {total['shorts']}, у Poster {total['published']}"))
                    last_new = time.time()
                    if not watch:
                        break
                    continue
                if not watch:
                    break
                left = "" if not (pend and idle_s) else f" · запис почнеться через {max(0, int(idle_s - idle))} с"
                q.put(("progress", (f"Чекаю реплеї в «Завантаженнях»… зібрано до запису: {len(pend)}{left}", 0.0)))
                for _ in range(15):          # перевірка папки кожні ~3 с
                    if cancel.is_set() or go_now.is_set():
                        break
                    time.sleep(0.2)
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
                    btn_now.config(state="disabled")
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
            messagebox.showinfo("Реплеї", "Виберіть хоча б одну дію: забрати реплеї, записати або нарізати.")
            return
        if params["do_record"] and not messagebox.askokcancel(
                "Запис реплеїв", "Коли почнеться запис, гра буде на весь екран — ПК тоді краще не чіпати "
                                 "(запис почнеться кнопкою «🎬 Записати зібрані» або сам, коли нових реплеїв не буде "
                                 f"{params['idle_min']} хв).\nOBS має бути відкритий, сцена — із захопленням гри. Почати?"):
            return
        cancel.clear()
        go_now.clear()
        running["on"] = True
        btn_go.config(state="disabled")
        btn_stop.config(state="normal")
        btn_now.config(state="normal" if (params["do_record"] or params["do_cut"]) else "disabled")
        if params["do_download"] and params["open_on_start"]:
            open_selection()
        logbox.delete("1.0", "end")
        threading.Thread(target=work, args=(params,), daemon=True, name="replays").start()

    def stop():
        cancel.set()
        lbl_stage.config(text="Зупиняю… (поточний крок завершиться)")

    btn_go.config(command=start)
    btn_stop.config(command=stop)
    refresh_tanks()
    refresh_version()
    poll()
    return outer


__all__ = ["build_replays_tab"]
