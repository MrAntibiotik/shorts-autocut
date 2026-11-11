"""Вікно запуску автонарізки: вибір запису, профіль гри, текст заклику -> обробка з прогресом.

Відкривається кнопкою в DaVinci (Workspace → Scripts → Shorts Autocut) або через extra\\run_job.bat.
Після завершення поверніться в DaVinci і натисніть ту саму кнопку ще раз — вона збере таймлайни.

Без вікна (для перевірки): python tools/run_job.py --cli ЗАПИС.mp4 --profile wot --cta "Підпишись!"
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import sys
import threading
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shorts.config import (list_profiles, load_profile_raw, load_settings, reset_profile,  # noqa: E402
                           strip_comments, work_dir)
from shorts.errors import UserError  # noqa: E402
from shorts.logs import setup_logging  # noqa: E402
from shorts.pipeline import Cancelled, Job, JobOptions, Progress  # noqa: E402
from shorts.resolve_export import read_status, write_status  # noqa: E402
from shorts.util import fmt_time, parse_time  # noqa: E402

VIDEO_TYPES = [("Відео", "*.mp4 *.mkv *.mov *.flv *.ts"), ("Усі файли", "*.*")]


def state_path(settings) -> Path:
    return work_dir(settings) / "ui_state.json"


def load_state(settings) -> dict:
    try:
        return json.loads(state_path(settings).read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(settings, data: dict):
    state_path(settings).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def request_from_resolve(settings) -> list[str]:
    """Шлях до кліпу, вибраного в медіапулі DaVinci (кнопка пише його в work/jobs/request.json)."""
    p = work_dir(settings) / "jobs" / "request.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        p.unlink()
        return [x for x in data.get("selected", []) if x and Path(x).exists()]
    except Exception:
        return []


class Heartbeat(threading.Thread):
    """Поки йде обробка, раз на 10 с оновлює статус (щоб кнопка в DaVinci бачила, що процес живий)."""

    def __init__(self, settings, info: dict):
        super().__init__(daemon=True)
        self.settings, self.info, self.stop = settings, info, False

    def run(self):
        while not self.stop:
            try:
                write_status(self.settings, stage=self.info.get("stage", ""), percent=self.info.get("percent", 0))
            except Exception:
                pass
            time.sleep(10)


class QueueHandler(logging.Handler):
    def __init__(self, q: queue.Queue):
        super().__init__(logging.INFO)
        self.q = q
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record):
        self.q.put(("log", self.format(record)))


def run_job_thread(settings, opts: JobOptions, q: queue.Queue, cancel: threading.Event):
    info = {"stage": "", "percent": 0}
    hb = Heartbeat(settings, info)
    hb.start()

    def cb(stage, frac):
        info["stage"], info["percent"] = stage, int(frac * 100)
        q.put(("progress", (stage, frac)))

    try:
        res = Job(settings, opts, Progress(callback=cb, cancel_flag=cancel.is_set)).run()
        q.put(("done", res))
    except Cancelled:
        write_status(settings, status="error", message="Скасовано користувачем")
        q.put(("cancelled", None))
    except UserError as e:
        write_status(settings, status="error", message=str(e))
        logging.getLogger("run_job").error("[ПОМИЛКА] %s", e)
        q.put(("error", str(e)))
    except Exception as e:  # непередбачена помилка — повний текст у лог
        logging.getLogger("run_job").error("Непередбачена помилка:\n%s", traceback.format_exc())
        write_status(settings, status="error", message=f"{type(e).__name__}: {e}")
        tb = traceback.extract_tb(e.__traceback__)
        where = f"{Path(tb[-1].filename).name}:{tb[-1].lineno}" if tb else "?"
        q.put(("error", f"Непередбачена помилка: {type(e).__name__}: {e}\nМісце: {where}\n"
                        "Повний текст — у лог-файлі (Режим розробника → «Останній лог»)."))
    finally:
        hb.stop = True


# ============================================================================== вікно

def gui(settings, log_file: Path):
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    state = load_state(settings)
    profiles = list_profiles()
    names = {}
    for p in profiles:
        try:
            names[p] = strip_comments(load_profile_raw(p)).get("name", p)
        except UserError:
            names[p] = p

    root = tk.Tk()
    root.title("Автонарізка шортсів")
    root.geometry("760x620")
    root.minsize(640, 520)
    pad = {"padx": 10, "pady": 4}

    # ---------------------------------------------------------------- екран налаштувань
    setup = tk.Frame(root)
    setup.pack(fill="both", expand=True)
    tk.Label(setup, text="Автонарізка шортсів", font=("Segoe UI", 15, "bold")).pack(anchor="w", **pad)

    tk.Label(setup, text="Запис(и) стріму:", font=("Segoe UI", 10, "bold")).pack(anchor="w", **pad)
    lst_frame = tk.Frame(setup)
    lst_frame.pack(fill="x", **pad)
    files = tk.Listbox(lst_frame, height=4, font=("Segoe UI", 9))
    files.pack(side="left", fill="x", expand=True)
    bcol = tk.Frame(lst_frame)
    bcol.pack(side="left", padx=6)

    recs_prof: list[tuple[str, str, str | None]] = []   # (запис, профіль гри, режим) — як у списку

    def add_paths(paths):
        """Для кожного запису: вибір гри + обов'язкова перевірка рамок (майстер)."""
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from video_wizard import VideoWizard
        def add(f, pid, mode):
            recs_prof.append((f, pid, mode))
            mname = ""
            if mode:
                mname = " · " + ("Засвіт" if mode == "assist" else "Шкода" if mode == "damage" else mode)
            files.insert("end", f"[{names.get(pid, pid)}{mname}]  {Path(f).name}    —  {Path(f).parent}")

        paths = list(paths)
        for i, f in enumerate(paths):
            wiz = VideoWizard(root, settings, f, profiles, names, state,
                              others_same=lambda pid: sum(1 for _, p, _m in recs_prof if p == pid),
                              remaining=len(paths) - i - 1)
            if not wiz.result:
                continue
            add(f, wiz.result, wiz.mode)
            save_state(settings, state)
            if wiz.apply_rest.get():
                for g in paths[i + 1:]:
                    add(g, wiz.result, wiz.mode)
                break

    preselected = request_from_resolve(settings)
    if preselected:
        root.after(300, lambda: add_paths(preselected))

    def add_files():
        sel = filedialog.askopenfilenames(title="Оберіть запис(и) стріму", filetypes=VIDEO_TYPES,
                                          initialdir=state.get("last_dir") or str(Path.home()))
        if sel:
            state["last_dir"] = str(Path(sel[0]).parent)
            add_paths(list(sel))

    tk.Button(bcol, text="Додати…", width=12, command=add_files).pack(pady=2)
    def remove_sel():
        for i in reversed(files.curselection()):
            files.delete(i)
            del recs_prof[i]

    tk.Button(bcol, text="Прибрати", width=12, command=remove_sel).pack(pady=2)

    row = tk.Frame(setup)
    row.pack(fill="x", **pad)
    tk.Label(row, text="Налаштування гри:", font=("Segoe UI", 10, "bold")).pack(side="left")
    prof_var = tk.StringVar()
    combo = ttk.Combobox(row, state="readonly", width=30, values=[f"{names[p]}  ({p})" for p in profiles],
                         textvariable=prof_var)
    combo.pack(side="left", padx=8)

    def reset_settings():
        if combo.current() < 0:
            return
        prof = profiles[combo.current()]
        ans = messagebox.askyesnocancel(
            "Скинути налаштування гри",
            f"Повернути стандартні налаштування для «{names.get(prof, prof)}»?\n\n"
            "«Так» — скинути все, але ЗАЛИШИТИ ваші обведені рамки.\n"
            "«Ні» — скинути все, разом із рамками (їх доведеться обвести знову).\n"
            "«Скасувати» — нічого не міняти.\n\n"
            "Поточна версія збережеться як .json.bak.")
        if ans is None:
            return
        try:
            path = reset_profile(prof, keep_zones=ans)
        except UserError as e:
            messagebox.showerror("Помилка", str(e))
            return
        messagebox.showinfo("Готово", f"Налаштування «{names.get(prof, prof)}» скинуто"
                                      f"{' (рамки збережено)' if ans else ''}.\n{path}")

    tk.Button(row, text="Скинути налаштування…", command=reset_settings).pack(side="left")

    def clear_cache_ui():
        from shorts.cleanup import cache_size, clear_cache, human_size
        c, r = cache_size(settings, False), cache_size(settings, True)
        ans = messagebox.askyesnocancel(
            "Очистити кеш",
            f"Кеш і тимчасові файли: {human_size(c)}\n"
            f"Звіти тестів і debug-картинки: {human_size(r - c)}\n\n"
            "«Так» — видалити все це.\n"
            "«Ні» — видалити лише кеш (звіти й debug залишити).\n"
            "«Скасувати» — нічого не міняти.\n\n"
            "Готові шортси, налаштування, рамки й оригінали записів НЕ видаляються.\n"
            "Наступний запуск розпізнає записи заново (довше), далі — знову швидко.")
        if ans is None:
            return
        freed, failed = clear_cache(settings, with_reports=ans)
        messagebox.showinfo("Готово", f"Звільнено {human_size(freed)}."
                            + (f"\nНе вдалося видалити {failed} (файл відкритий в іншій програмі)." if failed else ""))

    tk.Button(row, text="Очистити кеш…", command=clear_cache_ui).pack(side="left", padx=6)
    last = state.get("profile", "wot")
    if last in profiles:
        combo.current(profiles.index(last))
    elif profiles:
        combo.current(0)

    row2 = tk.Frame(setup)
    row2.pack(fill="x", **pad)
    tk.Label(row2, text="Відрізок (необов'язково):  з").pack(side="left")
    e_from = tk.Entry(row2, width=10)
    e_from.pack(side="left", padx=4)
    tk.Label(row2, text="до").pack(side="left")
    e_to = tk.Entry(row2, width=10)
    e_to.pack(side="left", padx=4)
    tk.Label(row2, text="(напр. 1:20:00; порожньо = весь запис)", fg="#666").pack(side="left", padx=4)

    tk.Label(setup, text="Заклик у кінці шортсу (порожньо = без заклику):",
             font=("Segoe UI", 10, "bold")).pack(anchor="w", **pad)
    cta = tk.Text(setup, height=2, font=("Segoe UI", 11), wrap="word")
    cta.pack(fill="x", **pad)
    cta.insert("1.0", state.get("cta", "Підписуйся, далі буде ще більше! 🔥"))

    opts_frame = tk.Frame(setup)
    opts_frame.pack(fill="x", **pad)
    v_render = tk.BooleanVar(value=state.get("render", True))
    v_subs = tk.BooleanVar(value=state.get("subtitles_on", bool(settings["whisper"]["enabled"])))
    v_sep = tk.BooleanVar(value=state.get("separation", settings["separation"]["enabled"]))
    tk.Checkbutton(opts_frame, text="Рендерити готові MP4", variable=v_render).pack(anchor="w")
    tk.Checkbutton(opts_frame, text="Субтитри через whisper.cpp (експериментально; спершу extra\\setup_whisper.bat)", variable=v_subs).pack(anchor="w")
    v_sai = tk.BooleanVar(value=state.get("speech_ai_on", bool(settings.get("speech_ai", {}).get("enabled", False))))
    tk.Checkbutton(opts_frame, text="Повні фрази + субтитри (експериментально; спершу extra\\setup_speech_ai.bat)", variable=v_sai).pack(anchor="w")
    tk.Checkbutton(opts_frame, text="Відділяти голос від гри (нейромережа)", variable=v_sep).pack(anchor="w")

    st = read_status(settings)
    if st and st.get("status") == "done" and not (work_dir(settings) / "jobs" / f"{st.get('job_id')}.imported").exists():
        tk.Label(setup, text="Є готовий результат, ще не імпортований у DaVinci — натисніть кнопку в DaVinci.",
                 fg="#a05a00", wraplength=700, justify="left").pack(anchor="w", **pad)

    btns = tk.Frame(setup)
    btns.pack(fill="x", side="bottom", pady=10)

    # ---------------------------------------------------------------- екран прогресу
    prog = tk.Frame(root)
    stage_lbl = tk.Label(prog, text="", font=("Segoe UI", 11, "bold"), anchor="w", justify="left", wraplength=720)
    stage_lbl.pack(fill="x", **pad)
    bar = ttk.Progressbar(prog, maximum=1000)
    bar.pack(fill="x", **pad)
    eta_lbl = tk.Label(prog, text="", fg="#555", anchor="w")
    eta_lbl.pack(fill="x", **pad)
    logbox = tk.Text(prog, height=20, font=("Consolas", 9), state="disabled", wrap="word")
    logbox.pack(fill="both", expand=True, **pad)
    pbtns = tk.Frame(prog)
    pbtns.pack(fill="x", pady=8)

    q: queue.Queue = queue.Queue()
    cancel = threading.Event()
    t_start = [0.0]
    result = {}

    def append(text):
        logbox.configure(state="normal")
        logbox.insert("end", text + "\n")
        logbox.see("end")
        logbox.configure(state="disabled")

    def poll():
        try:
            while True:
                kind, data = q.get_nowait()
                if kind == "log":
                    append(data)
                elif kind == "progress":
                    stage, frac = data
                    stage_lbl.config(text=stage)
                    bar["value"] = frac * 1000
                    el = time.time() - t_start[0]
                    if frac > 0.02:
                        eta_lbl.config(text=f"{frac * 100:.0f} %   минуло {fmt_time(el, 0)}, "
                                            f"залишилось ≈ {fmt_time(el / frac - el, 0)}")
                elif kind == "done":
                    result.update(data)
                    total = sum(len(r["shorts"]) for r in data["recordings"])
                    bar["value"] = 1000
                    eta_lbl.config(text=f"100 %   готово за {fmt_time(time.time() - t_start[0], 0)}")
                    stage_lbl.config(text=f"Готово! Шортсів: {total}.\nПоверніться в DaVinci і натисніть "
                                          "Workspace → Scripts → Shorts Autocut ще раз — таймлайни зберуться.")
                    finish()
                elif kind in ("error", "cancelled"):
                    stage_lbl.config(text="Скасовано." if kind == "cancelled" else "Помилка — див. текст нижче.")
                    if kind == "error":
                        append("\n[ПОМИЛКА] " + data)
                        messagebox.showerror("Помилка", data)
                    finish()
        except queue.Empty:
            pass
        root.after(200, poll)

    def finish():
        stop_btn.pack_forget()
        if result.get("job_dir"):
            tk.Button(pbtns, text="Відкрити папку з результатами", command=lambda: open_folder(result["job_dir"])
                      ).pack(side="left", padx=10)

            def edit_subs():
                sys.path.insert(0, str(Path(__file__).resolve().parent))
                from subs_editor import SubsEditor
                SubsEditor(root, settings, Path(result["job_dir"]))

            tk.Button(pbtns, text="Редагувати субтитри…", command=edit_subs).pack(side="left")
        tk.Button(pbtns, text="Закрити", command=root.destroy).pack(side="right", padx=10)

    def start():
        recs = [r for r, _, _m in recs_prof]
        if not recs:
            messagebox.showwarning("Немає запису", "Додайте хоча б один файл запису.")
            return
        missing = [r for r in recs if not Path(r).exists()]
        if missing:
            messagebox.showerror("Файл не знайдено", "\n".join(missing))
            return
        if v_sai.get():
            from shorts.speech_ai import available
            ok, why = available()
            if not ok:
                ans = messagebox.askyesnocancel(
                    "Повні фрази + субтитри — не встановлено",
                    "Для цієї галочки ще не встановлено бібліотеки (Silero VAD, faster-whisper).\n\n"
                    "«Так» — відкрити встановлення зараз (extra\\setup_speech_ai.bat, ~3 ГБ моделі); "
                    "після нього натисніть «Почати» ще раз.\n"
                    "«Ні» — почати без цієї функції (без субтитрів, межі мови — як раніше).\n"
                    "«Скасувати» — нічого не робити.")
                if ans is None:
                    return
                if ans:
                    bat = Path(__file__).resolve().parent.parent / "extra" / "setup_speech_ai.bat"
                    if os.name == "nt":
                        import subprocess
                        subprocess.Popen(["cmd", "/c", str(bat)], cwd=str(bat.parent.parent),
                                         creationflags=subprocess.CREATE_NEW_CONSOLE)  # type: ignore[attr-defined]
                    return
                v_sai.set(False)
        if not v_sep.get() and (v_sai.get() or v_subs.get()):
            if not messagebox.askyesno(
                    "Субтитри без відділення голосу",
                    "Вимкнено «Відділяти голос від гри» — субтитри розпізнаватимуться з міксу разом зі "
                    "звуками гри, помилок буде набагато більше.\n\nВсе одно продовжити?"):
                return
        used = sorted({p for _, p, _m in recs_prof})
        if len(used) > 1:
            messagebox.showerror("Різні ігри", "В одному запуску — записи однієї гри. Зараз у списку: "
                                 + ", ".join(names.get(p, p) for p in used) + ". Приберіть зайві.")
            return
        prof = used[0]
        try:
            a = parse_time(e_from.get())
            b = parse_time(e_to.get())
        except ValueError as e:
            messagebox.showerror("Відрізок", str(e))
            return
        text = cta.get("1.0", "end").strip()
        state.update(profile=prof, cta=text, render=v_render.get(), subtitles_on=v_subs.get(),
                     speech_ai_on=v_sai.get(), separation=v_sep.get(), last_dir=state.get("last_dir") or str(Path(recs[0]).parent))
        save_state(settings, state)
        opts = JobOptions(recordings=[Path(r) for r in recs], profile_id=prof, cta_text=text, start=a, end=b,
                          render=v_render.get(), subtitles=v_subs.get(), separation=v_sep.get(),
                          speech_ai=v_sai.get(),
                          modes={str(Path(r)): m for r, _, m in recs_prof if m})
        setup.pack_forget()
        prog.pack(fill="both", expand=True)
        h = QueueHandler(q)
        logging.getLogger().addHandler(h)
        append(f"Лог-файл: {log_file}")
        t_start[0] = time.time()
        threading.Thread(target=run_job_thread, args=(settings, opts, q, cancel), daemon=True).start()

    def stop():
        if messagebox.askyesno("Зупинити", "Зупинити обробку? Уже оброблені частини збережуться, "
                                           "наступний запуск продовжить з них."):
            cancel.set()
            stage_lbl.config(text="Зупиняю…")

    tk.Button(btns, text="Почати", font=("Segoe UI", 11, "bold"), bg="#3a7d44", fg="white", width=16,
              command=start).pack(side="right", padx=10)
    tk.Button(btns, text="Закрити", width=12, command=root.destroy).pack(side="right")

    # ---------------------------------------------------------------- режим розробника («antibiotik»)
    def selected_recording():
        sel = files.curselection()
        items = [r for r, _, _m in recs_prof]
        return items[sel[0]] if sel else (items[0] if items else None)

    dev_btn = tk.Button(btns, text="🛠 Розробник", width=14)

    def set_dev(on: bool):
        state["dev_mode"] = on
        save_state(settings, state)
        if on and not dev_btn.winfo_ismapped():
            dev_btn.pack(side="left", padx=10)
        elif not on:
            dev_btn.pack_forget()

    def show_dev():
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from dev_panel import open_dev_panel
        set_dev(True)
        open_dev_panel(root, settings, selected_recording, on_disable=lambda: set_dev(False))

    dev_btn.config(command=show_dev)
    if state.get("dev_mode"):
        dev_btn.pack(side="left", padx=10)
    key_buf = [""]

    def on_key(event):
        ch = (event.char or "").lower()
        if not ch or not ch.isprintable():
            return
        key_buf[0] = (key_buf[0] + ch)[-20:]
        from dev_panel import is_secret
        if is_secret(key_buf[0]):
            key_buf[0] = ""
            show_dev()

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    root.bind_all("<Key>", on_key, add="+")
    stop_btn = tk.Button(pbtns, text="Зупинити", command=stop)
    stop_btn.pack(side="right", padx=10)

    def on_close():
        if prog.winfo_ismapped() and not result and not cancel.is_set() and stop_btn.winfo_ismapped():
            if not messagebox.askyesno("Вийти", "Обробка ще триває. Перервати й закрити?"):
                return
            cancel.set()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.after(200, poll)
    if preselected:
        root.after(300, lambda: stage_lbl.config(text=""))
    root.mainloop()


def open_folder(path: str):
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]


def main():
    ap = argparse.ArgumentParser(description="Автонарізка шортсів")
    ap.add_argument("--cli", nargs="*", help="без вікна: файли записів")
    ap.add_argument("--profile", default="wot")
    ap.add_argument("--cta", default="")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--subtitles", action="store_true", help="зробити субтитри (експериментально)")
    ap.add_argument("--no-separation", action="store_true")
    args = ap.parse_args()
    settings = load_settings()
    log_file = setup_logging("run_job", work_dir(settings) / "logs")

    st = read_status(settings)
    if st and st.get("status") == "running" and time.time() - st.get("heartbeat", 0) < 60:
        msg = f"Обробка вже йде ({st.get('stage', '')}, {st.get('percent', 0)} %). Дочекайтесь завершення."
        if args.cli is None:
            import tkinter as tk
            from tkinter import messagebox
            r = tk.Tk()
            r.withdraw()
            messagebox.showinfo("Автонарізка шортсів", msg)
            r.destroy()
        else:
            print(msg)
        return 1

    if args.cli is not None:
        opts = JobOptions(recordings=[Path(p) for p in args.cli], profile_id=args.profile, cta_text=args.cta,
                          start=parse_time(args.start), end=parse_time(args.end), render=not args.no_render,
                          subtitles=args.subtitles, separation=not args.no_separation,
                          speech_ai=bool(settings.get("speech_ai", {}).get("enabled", False)))
        try:
            res = Job(settings, opts).run()
        except UserError as e:
            write_status(settings, status="error", message=str(e))
            print(f"\n[ПОМИЛКА] {e}")
            return 1
        print(f"\nРезультат: {res['job_dir']}")
        return 0
    gui(settings, log_file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
