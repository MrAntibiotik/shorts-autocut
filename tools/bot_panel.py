"""Вкладка «Telegram-бот» у вікні автонарізки: токен, власник, стан, запуск/зупинка, автозапуск,
папка публікації, тестове повідомлення, лог. Те саме, що extra\\setup_telegram.bat, але кнопками."""

from __future__ import annotations

import os
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS.parent))
sys.path.insert(0, str(TOOLS))

from setup_telegram import STARTUP_LNK, bot_running, make_startup, start_bot, stop_bot  # noqa: E402
from telegram_bot import TgApi, load_cfg, save_cfg  # noqa: E402
from shorts.config import output_dir, work_dir  # noqa: E402


def build_bot_tab(parent: tk.Misc, settings: dict) -> tk.Frame:
    f = tk.Frame(parent, padx=12, pady=10)
    cfg = load_cfg()
    st = {"username": cfg.get("username"), "waiting": False}

    tk.Label(f, text="Telegram-бот", font=("Segoe UI", 15, "bold")).pack(anchor="w")
    tk.Label(f, text="Статус обробки, перегляд шортсів 480p, «Підходить → до публікації», «Видалити», оновлення — "
                     "з телефона. Бот працює на цьому ПК і відповідає лише вам.",
             fg="#555", wraplength=720, justify="left").pack(anchor="w", pady=(0, 8))

    # ---------------------------------------------------------------- стан
    box = tk.LabelFrame(f, text=" Стан ", padx=8, pady=6)
    box.pack(fill="x")
    lbl_run = tk.Label(box, font=("Segoe UI", 11, "bold"), anchor="w")
    lbl_run.pack(anchor="w")
    lbl_info = tk.Label(box, anchor="w", justify="left", font=("Segoe UI", 9))
    lbl_info.pack(anchor="w")
    row = tk.Frame(box)
    row.pack(fill="x", pady=(6, 0))

    def refresh():
        c = load_cfg()
        on = bot_running()
        lbl_run.config(text="● Працює" if on else ("○ Зупинено" if c.get("token") else "○ Не налаштовано"),
                       fg="#2e7d32" if on else "#a00")
        owner = c.get("owner_id")
        lines = [f"Бот: @{c.get('username') or st['username'] or '…'}" if c.get("token") else "Токен: не вказано",
                 f"Власник (ваш Telegram ID): {owner}" if owner else
                 ("Власник: ще ні — напишіть боту /start" if c.get("token") else ""),
                 f"Автозапуск з Windows: {'так' if STARTUP_LNK.exists() else 'ні'}",
                 f"Папка публікації: {c.get('publish_dir') or output_dir(settings) / '_до_публікації'}"]
        lbl_info.config(text="\n".join(x for x in lines if x))
        v_auto.set(STARTUP_LNK.exists())

    def do_start():
        if not load_cfg().get("token"):
            messagebox.showinfo("Telegram-бот", "Спершу вставте токен і натисніть «Перевірити й зберегти».")
            return
        start_bot()
        f.after(1500, refresh)

    def do_stop():
        stop_bot()
        f.after(800, refresh)

    def open_bot():
        u = load_cfg().get("username") or st["username"]
        if u:
            webbrowser.open(f"https://t.me/{u}")

    def test_msg():
        c = load_cfg()
        if not (c.get("token") and c.get("owner_id")):
            messagebox.showinfo("Telegram-бот", "Потрібні токен і власник (напишіть боту /start).")
            return

        def work():
            try:
                TgApi(c["token"]).call("sendMessage", chat_id=c["owner_id"], text="👋 Тест з вікна автонарізки — "
                                                                                   "бот на зв'язку.")
                msg = "Надіслано — перевірте Telegram."
            except Exception as e:
                msg = f"Не вийшло: {e}"
            f.after(0, lambda: messagebox.showinfo("Тестове повідомлення", msg))
        threading.Thread(target=work, daemon=True).start()

    def open_log():
        logs = sorted((work_dir(settings) / "logs").glob("telegram_bot_*.log"))
        if logs and os.name == "nt":
            os.startfile(str(logs[-1]))   # type: ignore[attr-defined]
        elif not logs:
            messagebox.showinfo("Лог", "Бот ще не запускався.")

    row2 = tk.Frame(box)
    row2.pack(fill="x", pady=(4, 0))
    for r, items in ((row, (("▶ Запустити / перезапустити", do_start), ("■ Зупинити", do_stop))),
                     (row2, (("Відкрити бота в Telegram", open_bot), ("Тестове повідомлення", test_msg),
                             ("Лог бота", open_log)))):
        for text, cmd in items:
            tk.Button(r, text=text, command=cmd).pack(side="left", padx=(0, 6))

    # ---------------------------------------------------------------- токен
    tb = tk.LabelFrame(f, text=" Підключення ", padx=8, pady=6)
    tb.pack(fill="x", pady=(10, 0))
    tk.Label(tb, text="1) У Telegram: @BotFather → /newbot → отримайте токен.  2) Вставте його сюди (Ctrl+V) і "
                      "натисніть «Перевірити й зберегти».  3) Напишіть своєму боту /start.",
             wraplength=720, justify="left", fg="#333").pack(anchor="w")
    trow = tk.Frame(tb)
    trow.pack(fill="x", pady=4)
    tk.Label(trow, text="Токен:").pack(side="left")
    e_tok = tk.Entry(trow, width=58, show="•")
    e_tok.pack(side="left", padx=6)
    if cfg.get("token"):
        e_tok.insert(0, cfg["token"])
    v_show = tk.BooleanVar(value=False)
    tk.Checkbutton(trow, text="показати", variable=v_show,
                   command=lambda: e_tok.config(show="" if v_show.get() else "•")).pack(side="left")
    lbl_tok = tk.Label(tb, text="", anchor="w", justify="left", wraplength=720)
    lbl_tok.pack(anchor="w")

    def wait_owner(token: str):
        """Чекаємо /start від власника (до 3 хв), не блокуючи вікно."""
        api = TgApi(token)
        offset, t_end = None, time.time() + 180
        while time.time() < t_end:
            try:
                for u in api.call("getUpdates", _timeout=35, timeout=25, offset=offset):
                    offset = u["update_id"] + 1
                    m = u.get("message") or {}
                    if (m.get("text") or "").startswith("/start"):
                        c = load_cfg()
                        c["owner_id"] = m["from"]["id"]
                        save_cfg(c)
                        api.call("getUpdates", _timeout=20, offset=offset, timeout=0)
                        api.call("sendMessage", chat_id=c["owner_id"], text="✅ Бот підключено! /status, /shorts, /update.")
                        f.after(0, lambda: (lbl_tok.config(text="✅ Власника збережено. Запускаю бота…", fg="#2e7d32"),
                                            do_start()))
                        st["waiting"] = False
                        return
            except Exception:
                time.sleep(3)
        st["waiting"] = False
        f.after(0, lambda: lbl_tok.config(text="/start не прийшов за 3 хв — натисніть «Перевірити й зберегти» ще раз.",
                                          fg="#a00"))

    def check_save():
        token = e_tok.get().strip().strip('"')
        if not token:
            return
        lbl_tok.config(text="Перевіряю…", fg="#555")

        def work():
            try:
                me = TgApi(token).call("getMe", _timeout=20)
            except Exception as e:
                err = str(e)
                f.after(0, lambda: lbl_tok.config(text=f"Токен не підходить або немає інтернету: {err}", fg="#a00"))
                return
            c = load_cfg()
            changed = c.get("token") != token
            c.update(token=token, username=me.get("username"))
            if changed:
                c.pop("owner_id", None)
            save_cfg(c)
            st["username"] = me.get("username")
            if c.get("owner_id"):
                f.after(0, lambda: (lbl_tok.config(text=f"✅ Токен дійсний: @{me.get('username')}", fg="#2e7d32"),
                                    do_start()))
                return
            stop_bot()                       # старий бот (інший токен) не повинен «з'їсти» /start
            st["waiting"] = True
            f.after(0, lambda: (lbl_tok.config(text=f"✅ Бот @{me.get('username')}. Тепер відкрийте його в Telegram і "
                                                    "натисніть «Почати» (/start) — чекаю до 3 хв…", fg="#2a5d9f"),
                                open_bot(), refresh()))
            wait_owner(token)
        threading.Thread(target=work, daemon=True).start()

    brow = tk.Frame(tb)
    brow.pack(fill="x")
    tk.Button(brow, text="Перевірити й зберегти", font=("Segoe UI", 10, "bold"), command=check_save).pack(side="left")

    def reset_owner():
        if messagebox.askyesno("Власник", "Скинути власника? Наступний, хто напише боту /start, стане власником."):
            c = load_cfg()
            c.pop("owner_id", None)
            save_cfg(c)
            check_save()

    tk.Button(brow, text="Скинути власника", command=reset_owner).pack(side="left", padx=6)

    # ---------------------------------------------------------------- налаштування
    ob = tk.LabelFrame(f, text=" Налаштування ", padx=8, pady=6)
    ob.pack(fill="x", pady=(10, 0))
    v_auto = tk.BooleanVar(value=STARTUP_LNK.exists())

    def toggle_auto():
        make_startup(v_auto.get())
        refresh()

    tk.Checkbutton(ob, text="Запускати бота разом з Windows (рекомендовано)", variable=v_auto,
                   command=toggle_auto).pack(anchor="w")
    tk.Label(ob, text="Папка публікації (кнопка «✅ Підходить» у боті переносить туди MP4):").pack(anchor="w",
                                                                                              pady=(4, 0))
    prow = tk.Frame(ob)
    prow.pack(fill="x", pady=(0, 4))
    e_pub = tk.Entry(prow, width=62)
    e_pub.insert(0, cfg.get("publish_dir") or str(output_dir(settings) / "_до_публікації"))
    e_pub.pack(side="left", padx=(0, 6))

    def pick_pub():
        d = filedialog.askdirectory(title="Папка для шортсів до публікації", initialdir=e_pub.get() or None)
        if d:
            e_pub.delete(0, "end")
            e_pub.insert(0, d)
            save_pub()

    def save_pub():
        c = load_cfg()
        p = Path(e_pub.get().strip().strip('"'))
        p.mkdir(parents=True, exist_ok=True)
        c["publish_dir"] = str(p)
        save_cfg(c)
        refresh()
        if bot_running():
            start_bot()                     # перезапуск — щоб бот узяв нову папку

    tk.Button(prow, text="Огляд…", command=pick_pub).pack(side="left")
    tk.Button(prow, text="Зберегти", command=save_pub).pack(side="left", padx=4)

    tk.Label(f, text="Команди в боті: /status, /shorts, /all, /kept, /update. Токен і ваш ID зберігаються лише на "
                     "цьому ПК (config\\telegram.json).", fg="#666", wraplength=720, justify="left").pack(anchor="w",
                                                                                                         pady=(10, 0))

    def tick():
        try:
            refresh()
        finally:
            f.after(4000, tick)

    tick()
    return f
