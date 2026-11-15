"""Вікно «⏪ Відкат»: список збережених версій (backups\\) і повернення вибраної.

Налаштування, рамки, токен бота, відео й кеш не змінюються. Поточна версія теж зберігається —
відкат можна скасувати так само.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

from autocut.cli import update


class RollbackDialog:
    def __init__(self, root: tk.Misc):
        self.root = root
        self.items = update.list_backups()
        w = self.w = tk.Toplevel(root)
        w.title("Відкат версії")
        w.transient(root)
        tk.Label(w, text=f"Зараз встановлено версію {update.code_version()}",
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=12, pady=(12, 4))
        if not self.items:
            tk.Label(w, text="Збережених версій ще немає: копія робиться перед кожним оновленням.",
                     fg="#555").pack(anchor="w", padx=12, pady=8)
            tk.Button(w, text="Закрити", command=w.destroy).pack(pady=(0, 12))
            return
        tk.Label(w, text="Виберіть версію, на яку повернутись. Налаштування, рамки, відео й кеш не змінюються.",
                 fg="#555").pack(anchor="w", padx=12)
        self.lst = tk.Listbox(w, width=90, height=min(8, len(self.items)), font=("Segoe UI", 10))
        for b in self.items:
            self.lst.insert("end", f"  {b['version']}   ·   збережено {b['created']}   ·   {b.get('message', '')[:60]}")
        self.lst.selection_set(0)
        self.lst.pack(fill="x", padx=12, pady=8)
        bt = tk.Frame(w)
        bt.pack(fill="x", padx=12, pady=(0, 12))
        tk.Button(bt, text="⏪ Повернути вибрану", bg="#a05a00", fg="white", command=self.go).pack(side="right")
        tk.Button(bt, text="Скасувати", command=w.destroy).pack(side="right", padx=8)
        w.grab_set()

    def go(self):
        sel = self.lst.curselection()
        if not sel:
            return
        b = self.items[sel[0]]
        if not messagebox.askyesno("Відкат версії", f"Повернути версію {b['version']} (збережено {b['created']})?\n\n"
                                   "Після цього закрийте програму й відкрийте знову (бот перезапуститься сам).", parent=self.w):
            return
        try:
            res = update.restore_backup(b["name"], log=lambda m: None)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Відкат версії", f"Не вдалося: {e}", parent=self.w)
            return
        bot = ""
        try:                                   # бот працює на старому коді в пам'яті — перезапустити
            from autocut.bot.setup_telegram import bot_running, start_bot
            if bot_running():
                start_bot()
                bot = "\nTelegram-бот перезапущено."
        except Exception as e:  # noqa: BLE001
            bot = f"\nTelegram-бот перезапустіть вручну ({e})."
        messagebox.showinfo("Відкат версії", f"Повернуто версію {res['version']}.{bot}\n"
                            "Закрийте програму й відкрийте знову.", parent=self.w)
        self.w.destroy()
