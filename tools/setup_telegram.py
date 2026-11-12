"""Налаштування Telegram-бота автонарізки (extra\\setup_telegram.bat).

1. Створіть бота: у Telegram напишіть @BotFather → /newbot → назва → отримаєте ТОКЕН.
2. Вставте токен сюди.
3. Напишіть своєму боту /start — він запам'ятає вас як власника (іншим не відповідатиме).
4. Автозапуск з Windows (рекомендовано) — бот працює завжди, навіть коли вікно інструмента закрите.
Токен і ваш Telegram ID зберігаються лише на цьому ПК: config\\telegram.json.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from telegram_bot import CFG_FILE, LOCK_PORT, TgApi, load_cfg, save_cfg  # noqa: E402

STARTUP_LNK = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup/Shorts Telegram Bot.lnk"


def yes(prompt: str, default: bool = True) -> bool:
    a = input(f"{prompt} [{'Т/н' if default else 'т/Н'}]: ").strip().lower()
    return default if not a else a.startswith(("т", "y", "д"))


def make_startup(on: bool) -> None:
    if os.name != "nt":
        return
    if not on:
        if STARTUP_LNK.exists():
            STARTUP_LNK.unlink()
            print("  Автозапуск вимкнено.")
        return
    pyw = ROOT / "venv" / "Scripts" / "pythonw.exe"
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{STARTUP_LNK}');"
          f"$s.TargetPath='{pyw}';$s.Arguments='\"{ROOT / 'tools' / 'telegram_bot.py'}\"';"
          f"$s.WorkingDirectory='{ROOT}';$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=False)
    print(f"  Автозапуск увімкнено: {STARTUP_LNK.name}")


def bot_running() -> bool:
    import socket
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", LOCK_PORT))
        return False
    except OSError:
        return True
    finally:
        s.close()


def stop_bot() -> None:
    import socket
    if not bot_running():
        return
    try:
        with socket.create_connection(("127.0.0.1", LOCK_PORT), timeout=3) as s:
            s.sendall(b"stop")
        for _ in range(20):
            time.sleep(0.25)
            if not bot_running():
                break
        print("  Попередній бот зупинено.")
    except OSError:
        pass


def start_bot() -> None:
    stop_bot()                    # перезапуск — щоб підхопив новий токен
    pyw = ROOT / "venv" / "Scripts" / ("pythonw.exe" if os.name == "nt" else "python")
    subprocess.Popen([str(pyw), str(ROOT / "tools" / "telegram_bot.py")], cwd=str(ROOT),
                     creationflags=0x08000000 if os.name == "nt" else 0)
    print("  Бот запущено.")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    print("=" * 70)
    print(" Telegram-бот автонарізки")
    print("=" * 70)
    cfg = load_cfg()
    if cfg.get("token"):
        print(f"Бот уже налаштований (власник: {cfg.get('owner_id')}).")
        if not yes("Налаштувати заново (новий токен)?", False):
            make_startup(yes("Автозапуск разом з Windows?", True))
            start_bot()
            return 0
    print("\n1) У Telegram відкрийте @BotFather → /newbot → придумайте назву й ім'я (на ...bot).")
    print("   BotFather надішле ТОКЕН, схожий на 1234567890:AAH...\n")
    while True:
        token = input("Вставте токен (правий клік у цьому вікні = вставити): ").strip().strip('"')
        try:
            me = TgApi(token).call("getMe", _timeout=20)
            break
        except Exception as e:
            print(f"  Не вийшло: {e}\n  Перевірте токен і інтернет, спробуйте ще раз.")
    print(f"  Бот: @{me.get('username')}")
    api = TgApi(token)
    print(f"\n2) Відкрийте https://t.me/{me.get('username')} і натисніть «Почати» (/start). Чекаю до 3 хв…")
    owner, offset, t_end = None, None, time.time() + 180
    while owner is None and time.time() < t_end:
        try:
            for u in api.call("getUpdates", _timeout=40, timeout=25, offset=offset):
                offset = u["update_id"] + 1
                m = u.get("message") or {}
                if (m.get("text") or "").startswith("/start"):
                    owner = m["from"]["id"]
                    name = m["from"].get("first_name", "")
                    print(f"  Власник: {name} (ID {owner})")
        except Exception as e:
            print(f"  Зв'язок: {e}")
            time.sleep(3)
    if owner is None:
        print("[ПОМИЛКА] /start так і не прийшов. Запустіть налаштування ще раз.")
        return 1
    if offset is not None:
        api.call("getUpdates", _timeout=20, offset=offset, timeout=0)   # «прочитати» /start
    save_cfg({"token": token, "owner_id": owner})
    print(f"  Збережено: {CFG_FILE}")
    try:
        api.call("sendMessage", chat_id=owner, text="✅ Бот підключено! /status — що обробляється, "
                                                    "/shorts — готові шортси.")
    except Exception:
        pass
    print("\n3) Автозапуск")
    make_startup(yes("Запускати бота разом з Windows (рекомендовано)?", True))
    start_bot()
    print("\nГотово. Напишіть боту /status або /shorts.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(1)
