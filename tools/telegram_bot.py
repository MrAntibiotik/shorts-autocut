"""Telegram-бот автонарізки: статус обробки, перегляд готових шортсів (480p), «залишити» / «видалити».

Працює на вашому ПК (без сервера): сам питає Telegram про нові повідомлення. Відповідає ЛИШЕ власнику
(Telegram ID збережено в config\\telegram.json при налаштуванні). Налаштування: extra\\setup_telegram.bat.

Команди: /status — що зараз обробляється; /shorts — шортси останнього результату; /all — усі результати;
/kept — залишені; /help. Під кожним шортсом: ▶ 480p, 📱 оригінал, ✅ Залишити, 🗑 Видалити
(видалений шортс переноситься в output\\_видалені — його можна повернути вручну).
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shorts.config import CONFIG_DIR, load_settings, output_dir, work_dir  # noqa: E402
from shorts.logs import setup_logging  # noqa: E402
from shorts.resolve_export import read_status  # noqa: E402
from shorts.util import fmt_time  # noqa: E402

log = logging.getLogger("telegram")
CFG_FILE = CONFIG_DIR / "telegram.json"
LOCK_PORT = 47321                   # один бот на ПК
MAX_UPLOAD = 49 * 1024 * 1024       # ліміт Telegram для ботів — 50 МБ
DELETED_DIR = "_видалені"
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


# ====================================================================== налаштування

def load_cfg() -> dict:
    try:
        return json.loads(CFG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_cfg(cfg: dict) -> None:
    CFG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ====================================================================== Telegram API

class TgApi:
    def __init__(self, token: str):
        self.base = f"https://api.telegram.org/bot{token}/"

    def call(self, method: str, _timeout: float = 40, **params):
        data = json.dumps({k: v for k, v in params.items() if v is not None}).encode("utf-8")
        req = urllib.request.Request(self.base + method, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=_timeout) as r:
                res = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            raise RuntimeError(f"Telegram {method}: {e.code} {body[:300]}") from None
        if not res.get("ok"):
            raise RuntimeError(f"Telegram {method}: {res}")
        return res["result"]

    def send_file(self, method: str, field: str, path: Path, _timeout: float = 300, **params):
        """Надсилання файлу (sendVideo / sendDocument) — multipart/form-data."""
        boundary = uuid.uuid4().hex
        parts = []
        for k, v in params.items():
            if v is None:
                continue
            if isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False)
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode("utf-8"))
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{path.name}\"\r\n"
                f"Content-Type: {ctype}\r\n\r\n").encode("utf-8")
        body = b"".join(parts) + head + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode("utf-8")
        req = urllib.request.Request(self.base + method, data=body,
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(req, timeout=_timeout) as r:
                res = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Telegram {method}: {e.code} {e.read().decode('utf-8', 'replace')[:300]}") from None
        if not res.get("ok"):
            raise RuntimeError(f"Telegram {method}: {res}")
        return res["result"]


# ====================================================================== результати обробки

def list_jobs(settings: dict) -> list[Path]:
    out = output_dir(settings)
    return sorted((p for p in out.glob("job_*") if (p / "job.json").exists()), key=lambda p: p.name)


def load_job(job_dir: Path) -> dict | None:
    try:
        return json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    except Exception:
        return None


def job_shorts(job_dir: Path) -> list[dict]:
    """Шортси результату: [{i, name, mp4, dir, duration, deleted}] (i — номер у результаті)."""
    job = load_job(job_dir) or {}
    res = []
    i = 0
    for rec in job.get("recordings", []):
        for sh in rec.get("shorts", []):
            i += 1
            mp4 = Path(sh["mp4"]) if sh.get("mp4") else job_dir / sh.get("dir", "") / f"{sh['file_base']}.mp4"
            bdir = job_dir / sh["dir"] if sh.get("dir") else None
            res.append({"i": i, "name": sh.get("name", f"Шортс {i}"), "mp4": mp4, "dir": bdir,
                        "duration": float(sh.get("duration") or 0), "deleted": not mp4.exists()})
    return res


def short_key(job_dir: Path, i: int) -> str:
    return f"{job_dir.name.removeprefix('job_')}|{i}"


def find_short(settings: dict, key: str) -> tuple[Path, dict] | None:
    jid, _, i = key.partition("|")
    job_dir = output_dir(settings) / f"job_{jid}"
    for s in job_shorts(job_dir):
        if str(s["i"]) == i:
            return job_dir, s
    return None


class Marks:
    """Позначки «залишити» — output\\_telegram.json."""

    def __init__(self, settings: dict):
        self.path = output_dir(settings) / "_telegram.json"
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.data = {"kept": []}

    def is_kept(self, key: str) -> bool:
        return key in self.data.setdefault("kept", [])

    def set_kept(self, key: str, on: bool):
        kept = self.data.setdefault("kept", [])
        if on and key not in kept:
            kept.append(key)
        if not on and key in kept:
            kept.remove(key)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")


def make_preview(settings: dict, mp4: Path) -> Path:
    """Легка копія 480×854 для перегляду в Telegram (кешується)."""
    from shorts.media import find_tool
    d = work_dir(settings) / "tg_preview"
    d.mkdir(parents=True, exist_ok=True)
    st = mp4.stat()
    out = d / f"{mp4.stem}_{int(st.st_mtime)}_{st.st_size}_480.mp4"
    if out.exists():
        return out
    tmp = out.with_suffix(".tmp.mp4")
    cmd = [find_tool(settings, "ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-i", str(mp4),
           "-vf", "scale=480:-2", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
           "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(tmp)]
    r = subprocess.run(cmd, capture_output=True, creationflags=_NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace")[-400:])
    tmp.replace(out)
    return out


def delete_short(settings: dict, job_dir: Path, s: dict) -> Path:
    """Переносить папку бою (або файли шортсу) в output\\_видалені\\<результат>\\."""
    dst_root = output_dir(settings) / DELETED_DIR / job_dir.name
    dst_root.mkdir(parents=True, exist_ok=True)
    if s["dir"] and s["dir"].is_dir() and s["dir"] != job_dir:
        dst = dst_root / s["dir"].name
        shutil.move(str(s["dir"]), str(dst))
        return dst
    for f in s["mp4"].parent.glob(s["mp4"].stem + ".*"):
        shutil.move(str(f), str(dst_root / f.name))
    return dst_root


# ====================================================================== бот

class Bot:
    def __init__(self, settings: dict, cfg: dict, api: TgApi | None = None):
        self.settings = settings
        self.cfg = cfg
        self.api = api or TgApi(cfg["token"])
        self.owner = cfg.get("owner_id")
        self.offset = None
        self.last_status: tuple | None = None

    # -------------------------------------------------------------- відправка
    def send(self, text: str, markup: dict | None = None):
        return self.api.call("sendMessage", chat_id=self.owner, text=text, reply_markup=markup,
                             disable_web_page_preview=True)

    def short_markup(self, key: str, kept: bool) -> dict:
        return {"inline_keyboard": [
            [{"text": "▶ Переглянути 480p", "callback_data": f"v|{key}"},
             {"text": "📱 Оригінал", "callback_data": f"f|{key}"}],
            [{"text": "✅ Залишено" if kept else "✅ Залишити", "callback_data": f"k|{key}"},
             {"text": "🗑 Видалити", "callback_data": f"d|{key}"}],
        ]}

    def show_job(self, job_dir: Path, only_kept: bool = False):
        shorts = [s for s in job_shorts(job_dir) if not s["deleted"]]
        marks = Marks(self.settings)
        if only_kept:
            shorts = [s for s in shorts if marks.is_kept(short_key(job_dir, s["i"]))]
        if not shorts:
            return 0
        self.send(f"📁 {job_dir.name} — шортсів: {len(shorts)}")
        for s in shorts:
            key = short_key(job_dir, s["i"])
            mb = s["mp4"].stat().st_size / 1024 / 1024 if s["mp4"].exists() else 0
            self.send(f"🎬 {s['name']}\n⏱ {s['duration']:.0f} с · {mb:.1f} МБ",
                      self.short_markup(key, marks.is_kept(key)))
        return len(shorts)

    # -------------------------------------------------------------- команди
    def cmd_status(self):
        st = read_status(self.settings) or {}
        s = st.get("status")
        age = time.time() - float(st.get("heartbeat") or 0)
        if s == "running" and age < 120:
            self.send(f"⏳ Обробка йде: {st.get('stage', '')} — {st.get('percent', 0)} %"
                      + (f"\nРезультат: {Path(st['job_dir']).name}" if st.get("job_dir") else ""))
        elif s == "running":
            self.send(f"⚠️ Обробка, схоже, зупинилась (немає вістей {fmt_time(age, 0)}). "
                      f"Останній етап: {st.get('stage', '')}")
        elif s == "done":
            self.send(f"✅ Остання обробка завершена: {st.get('message', '')}\n/shorts — показати шортси")
        elif s == "error":
            self.send(f"❌ Остання обробка з помилкою: {st.get('message', '')}")
        else:
            self.send("Обробок ще не було.")

    def cmd_shorts(self):
        jobs = list_jobs(self.settings)
        for j in reversed(jobs):
            if self.show_job(j):
                return
        self.send("Готових шортсів поки немає.")

    def cmd_all(self):
        jobs = list_jobs(self.settings)[-12:]
        if not jobs:
            self.send("Результатів ще немає.")
            return
        rows = []
        for j in reversed(jobs):
            n = sum(1 for s in job_shorts(j) if not s["deleted"])
            rows.append([{"text": f"{j.name.removeprefix('job_')} — {n} шт.",
                          "callback_data": f"j|{j.name.removeprefix('job_')}"}])
        self.send("Результати (новіші — зверху):", {"inline_keyboard": rows})

    def cmd_kept(self):
        total = sum(self.show_job(j, only_kept=True) for j in reversed(list_jobs(self.settings)))
        if not total:
            self.send("Залишених шортсів ще немає (кнопка ✅ під шортсом).")

    HELP = ("Команди:\n/status — що зараз обробляється\n/shorts — шортси останнього результату\n"
            "/all — усі результати\n/kept — залишені шортси\n\n"
            "Під шортсом: ▶ 480p — легкий перегляд, 📱 оригінал (якщо до 50 МБ), ✅ залишити, "
            "🗑 видалити (переноситься в output\\_видалені).")

    # -------------------------------------------------------------- кнопки
    def on_callback(self, cq: dict):
        data = cq.get("data", "")
        act, _, key = data.partition("|")
        self.api.call("answerCallbackQuery", callback_query_id=cq["id"])
        if act == "j":
            self.show_job(output_dir(self.settings) / f"job_{key}")
            return
        found = find_short(self.settings, key)
        if not found:
            self.send("Цього шортсу вже немає.")
            return
        job_dir, s = found
        if s["deleted"]:
            self.send(f"«{s['name']}» уже видалено.")
            return
        msg = cq.get("message") or {}
        if act in ("v", "f"):
            self.api.call("sendChatAction", chat_id=self.owner, action="upload_video")
            path = s["mp4"]
            if act == "v":
                path = make_preview(self.settings, s["mp4"])
            if path.stat().st_size > MAX_UPLOAD:
                self.send("Оригінал більший за 50 МБ — Telegram не дозволяє ботам таке надсилати. "
                          "Дивіться ▶ 480p.")
                return
            w, h = (480, 854) if act == "v" else (1080, 1920)
            self.api.send_file("sendVideo", "video", path, chat_id=self.owner, caption=s["name"],
                               supports_streaming="true", width=w, height=h, duration=int(s["duration"]),
                               reply_markup=self.short_markup(key, Marks(self.settings).is_kept(key)))
        elif act == "k":
            marks = Marks(self.settings)
            on = not marks.is_kept(key)
            marks.set_kept(key, on)
            if msg:
                self.api.call("editMessageReplyMarkup", chat_id=self.owner, message_id=msg["message_id"],
                              reply_markup=self.short_markup(key, on))
        elif act == "d":
            if msg:
                self.api.call("editMessageReplyMarkup", chat_id=self.owner, message_id=msg["message_id"],
                              reply_markup={"inline_keyboard": [[
                                  {"text": "🗑 Так, видалити", "callback_data": f"D|{key}"},
                                  {"text": "Скасувати", "callback_data": f"c|{key}"}]]})
        elif act == "D":
            dst = delete_short(self.settings, job_dir, s)
            Marks(self.settings).set_kept(key, False)
            if msg and msg.get("text"):
                self.api.call("editMessageText", chat_id=self.owner, message_id=msg["message_id"],
                              text=f"🗑 Видалено: {s['name']}\n(перенесено в {dst.parent.parent.name}\\{dst.parent.name})")
            else:
                self.send(f"🗑 Видалено: {s['name']}")
        elif act == "c" and msg:
            self.api.call("editMessageReplyMarkup", chat_id=self.owner, message_id=msg["message_id"],
                          reply_markup=self.short_markup(key, Marks(self.settings).is_kept(key)))

    # -------------------------------------------------------------- цикл
    def handle(self, upd: dict):
        if "callback_query" in upd:
            cq = upd["callback_query"]
            if (cq.get("from") or {}).get("id") != self.owner:
                return
            self.on_callback(cq)
            return
        m = upd.get("message") or {}
        uid = (m.get("from") or {}).get("id")
        text = (m.get("text") or "").strip()
        if self.owner is None and text.startswith("/start"):
            self.owner = uid
            self.cfg["owner_id"] = uid
            save_cfg(self.cfg)
        if uid != self.owner:
            return                        # чужим — нічого
        cmd = text.split()[0].split("@")[0].lower() if text else ""
        if cmd == "/start":
            self.send("Привіт! Я бот автонарізки шортсів.\n\n" + self.HELP)
        elif cmd == "/status":
            self.cmd_status()
        elif cmd == "/shorts":
            self.cmd_shorts()
        elif cmd == "/all":
            self.cmd_all()
        elif cmd == "/kept":
            self.cmd_kept()
        else:
            self.send(self.HELP)

    def watch_status(self):
        """Повідомлення: почалась обробка / готово / помилка."""
        st = read_status(self.settings) or {}
        cur = (st.get("status"), st.get("job_id"))
        if self.last_status is None:
            self.last_status = cur
            return
        if cur == self.last_status or self.owner is None:
            return
        self.last_status = cur
        s = cur[0]
        if s == "running":
            self.send(f"▶ Почалась обробка ({cur[1]}). /status — прогрес")
        elif s == "done":
            self.send(f"✅ {st.get('message') or 'Готово'}",
                      {"inline_keyboard": [[{"text": "Показати шортси",
                                             "callback_data": f"j|{str(cur[1] or '').removeprefix('job_')}"}]]})
        elif s == "error":
            self.send(f"❌ Обробка завершилась з помилкою: {st.get('message', '')}")

    def run(self):
        try:
            self.api.call("setMyCommands", commands=[
                {"command": "status", "description": "Що зараз обробляється"},
                {"command": "shorts", "description": "Шортси останнього результату"},
                {"command": "all", "description": "Усі результати"},
                {"command": "kept", "description": "Залишені шортси"}])
        except Exception as e:
            log.warning("setMyCommands: %s", e)
        log.info("Бот запущено, власник: %s", self.owner)
        stop = threading.Event()

        def watcher():
            while not stop.is_set():
                try:
                    self.watch_status()
                except Exception as e:
                    log.warning("Статус: %s", e)
                stop.wait(10)

        threading.Thread(target=watcher, daemon=True).start()
        while True:
            try:
                ups = self.api.call("getUpdates", _timeout=60, timeout=40, offset=self.offset,
                                    allowed_updates=["message", "callback_query"])
                for u in ups:
                    self.offset = u["update_id"] + 1
                    try:
                        self.handle(u)
                    except Exception as e:
                        log.exception("Помилка обробки повідомлення")
                        try:
                            self.send(f"Помилка: {e}")
                        except Exception:
                            pass
            except Exception as e:
                log.warning("Зв'язок з Telegram: %s — повтор через 15 с", e)
                time.sleep(15)


def single_instance() -> socket.socket | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", LOCK_PORT))
        s.listen(1)
        return s
    except OSError:
        return None


def main() -> int:
    settings = load_settings()
    setup_logging("telegram_bot", work_dir(settings) / "logs")
    cfg = load_cfg()
    if not cfg.get("token"):
        print("Бот не налаштований. Запустіть extra\\setup_telegram.bat.")
        return 1
    lock = single_instance()
    if lock is None:
        log.info("Бот уже запущено — другий не потрібен.")
        return 0

    def control():
        # команда «stop» від extra\setup_telegram.bat (новий токен) — завершити бота
        while True:
            try:
                c, _ = lock.accept()
                if c.recv(16).startswith(b"stop"):
                    log.info("Бот зупинено (налаштування)")
                    os._exit(0)
                c.close()
            except Exception:
                time.sleep(1)

    threading.Thread(target=control, daemon=True).start()
    Bot(settings, cfg).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
