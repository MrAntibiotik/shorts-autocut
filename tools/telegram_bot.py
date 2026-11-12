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
sys.path.insert(0, str(ROOT / "tools"))

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
    """Шортси «до публікації»: ключ шортсу -> шлях MP4 у папці публікації (output\\_telegram.json)."""

    def __init__(self, settings: dict):
        self.path = output_dir(settings) / "_telegram.json"
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.data = {}
        if not isinstance(self.data.get("published"), dict):
            self.data["published"] = {}

    def published(self, key: str) -> Path | None:
        p = self.data["published"].get(key)
        return Path(p) if p and Path(p).exists() else None

    def set(self, key: str, path: Path | None):
        if path is None:
            self.data["published"].pop(key, None)
        else:
            self.data["published"][key] = str(path)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")


def publish_dir(settings: dict, cfg: dict) -> Path:
    d = Path(cfg["publish_dir"]) if cfg.get("publish_dir") else output_dir(settings) / "_до_публікації"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _unique(dst: Path) -> Path:
    n = 2
    out = dst
    while out.exists():
        out = dst.with_name(f"{dst.stem}_{n}{dst.suffix}")
        n += 1
    return out


def move_with_srt(mp4: Path, dst_dir: Path) -> Path:
    """Переносить MP4 і його субтитри (той самий початок назви, .srt) у dst_dir. Повертає новий шлях MP4."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    new = _unique(dst_dir / mp4.name)
    shutil.move(str(mp4), str(new))
    srt = mp4.with_suffix(".srt")
    if srt.exists():
        shutil.move(str(srt), str(new.with_suffix(".srt")))
    return new


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


def delete_short(settings: dict, job_dir: Path, s: dict, cur: Path) -> Path:
    """Переносить шортс у output\\_видалені\\<результат>\\: усю папку бою (якщо шортс на місці)
    або лише MP4 + SRT (якщо він уже в папці публікації)."""
    dst_root = output_dir(settings) / DELETED_DIR / job_dir.name
    dst_root.mkdir(parents=True, exist_ok=True)
    if cur == s["mp4"] and s["dir"] and s["dir"].is_dir() and s["dir"] != job_dir:
        dst = _unique(dst_root / s["dir"].name)
        shutil.move(str(s["dir"]), str(dst))
        return dst
    return move_with_srt(cur, dst_root).parent


# ====================================================================== бот

class Bot:
    def __init__(self, settings: dict, cfg: dict, api: TgApi | None = None):
        self.settings = settings
        self.cfg = cfg
        self.api = api or TgApi(cfg["token"])
        self.owner = cfg.get("owner_id")
        self.offset = None
        self.last_status: tuple | None = None
        self.lock = None                       # сокет «один бот на ПК» (для перезапуску після оновлення)
        self.notified_sha = cfg.get("notified_sha")
        self.last_update_check = 0.0

    # -------------------------------------------------------------- відправка
    def send(self, text: str, markup: dict | None = None):
        return self.api.call("sendMessage", chat_id=self.owner, text=text, reply_markup=markup,
                             disable_web_page_preview=True)

    def short_markup(self, key: str, published: bool, video: bool = True) -> dict:
        """Кнопки під шортсом. video=True — повідомлення вже є відео (кнопка 480p не потрібна)."""
        second = ([{"text": "↩ Повернути", "callback_data": f"u|{key}"}] if published else
                  [{"text": "✅ Підходить → до публікації", "callback_data": f"k|{key}"}])
        first = [{"text": "📱 Оригінал (повна якість)", "callback_data": f"f|{key}"}]
        if not video:
            first.insert(0, {"text": "▶ Переглянути 480p", "callback_data": f"v|{key}"})
        rows = [first, second + [{"text": "🗑 Видалити", "callback_data": f"d|{key}"}]]
        if published:   # помітна плашка статусу над кнопками
            rows.insert(0, [{"text": "✅ ДО ПУБЛІКАЦІЇ ✅", "callback_data": f"n|{key}"}])
        return {"inline_keyboard": rows}

    def send_short(self, key: str, s: dict, cur: Path, pub: bool):
        """Шортс — одразу відеоповідомленням 480p (дивитись прямо в чаті), кнопки під відео.
        Не вийшло (немає ffmpeg, задовгий файл) — текстом з кнопкою «▶ Переглянути 480p»."""
        try:
            self.api.call("sendChatAction", chat_id=self.owner, action="upload_video")
            prev = make_preview(self.settings, cur)
            if prev.stat().st_size > MAX_UPLOAD:
                raise RuntimeError("завеликий")
            self.api.send_file("sendVideo", "video", prev, chat_id=self.owner,
                               caption=self.short_text(s, cur, pub), supports_streaming="true",
                               width=480, height=854, duration=int(s["duration"]),
                               reply_markup=self.short_markup(key, pub, video=True))
        except Exception as e:
            log.warning("Відео %s не надіслано: %s", s["name"], e)
            self.send(self.short_text(s, cur, pub), self.short_markup(key, pub, video=False))

    def locate(self, job_dir: Path, s: dict, marks: Marks) -> tuple[Path | None, bool]:
        """Де зараз MP4 шортсу: (шлях або None, чи в папці публікації)."""
        pub = marks.published(short_key(job_dir, s["i"]))
        if pub:
            return pub, True
        return (s["mp4"], False) if s["mp4"].exists() else (None, False)

    def short_text(self, s: dict, cur: Path, published: bool) -> str:
        mb = cur.stat().st_size / 1024 / 1024 if cur.exists() else 0
        head = ("✅✅✅ ПІДХОДИТЬ — у папці публікації ✅✅✅\n━━━━━━━━━━━━━━━━\n" if published
                else "⬜ Ще не вирішено: підходить чи видалити?\n")
        return (head + f"🎬 {s['name']}\n⏱ {s['duration']:.0f} с · {mb:.1f} МБ"
                + (f"\n📤 Файл: {cur.name}" if published else ""))

    def show_job(self, job_dir: Path, only_published: bool = False):
        marks = Marks(self.settings)
        items = []
        for s in job_shorts(job_dir):
            cur, pub = self.locate(job_dir, s, marks)
            if cur is None or (only_published and not pub):
                continue
            items.append((s, cur, pub))
        if not items:
            return 0
        self.send(f"📁 {job_dir.name} — шортсів: {len(items)}. Надсилаю відео…")
        for s, cur, pub in items:
            self.send_short(short_key(job_dir, s["i"]), s, cur, pub)
        return len(items)

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
        total = sum(self.show_job(j, only_published=True) for j in reversed(list_jobs(self.settings)))
        if total:
            self.send(f"📤 Папка публікації: {publish_dir(self.settings, self.cfg)}")
        else:
            self.send("У папці публікації поки нічого немає (кнопка «✅ Підходить» під шортсом).")

    # -------------------------------------------------------------- оновлення
    UPDATE_BTN = {"inline_keyboard": [[{"text": "🔄 Оновити зараз", "callback_data": "U|go"}]]}

    def cmd_update(self):
        import update
        try:
            lv, rv = update.local_version(), update.remote_version()
        except Exception as e:
            self.send(f"Не вдалося зв'язатися з GitHub: {e}")
            return
        if lv.get("sha") == rv["sha"]:
            self.send(f"✅ У вас остання версія ({rv['sha'][:7]}: {rv['message']}).")
            return
        news = update.changes_since(lv.get("sha"))
        text = "🔄 Є оновлення" + (f" — змін: {len(news)}:\n" + "\n".join(f"• {c}" for c in news) if news
                                   else f": {rv['message']}")
        self.send(text, self.UPDATE_BTN)

    def do_update(self):
        st = read_status(self.settings) or {}
        if st.get("status") == "running" and time.time() - float(st.get("heartbeat") or 0) < 120:
            self.send("⏳ Зараз іде обробка — оновлю, коли закінчиться (натисніть «Оновити» ще раз пізніше).")
            return
        import update
        self.send("🔄 Оновлюю з GitHub…")
        try:
            res = update.apply_update(log=lambda m: None)
        except Exception as e:
            self.send(f"❌ Оновлення не вдалося: {e}\nМожна запустити SETUP.bat на ПК.")
            return
        if res["up_to_date"]:
            self.send(f"✅ Уже остання версія ({res['sha'][:7]}).")
            return
        news = res.get("changes") or [res["message"]]
        self.send(f"✅ Оновлено до {res['sha'][:7]} (файлів: {res['files']}"
                  + (", бібліотеки доставлено" if res.get("requirements") else "") + "):\n"
                  + "\n".join(f"• {c}" for c in news)
                  + "\n\nБот перезапускається… Якщо відкрито вікно інструмента — закрийте й відкрийте його знову; "
                    "DaVinci — перезапустіть, щоб оновилась кнопка.")
        self.restart()

    def restart(self):
        """Новий процес бота з оновленим кодом; цей — завершується."""
        try:
            if self.lock is not None:
                self.lock.close()
        except Exception:
            pass
        exe = Path(sys.executable)
        pyw = exe.with_name("pythonw.exe") if exe.with_name("pythonw.exe").exists() else exe
        subprocess.Popen([str(pyw), str(ROOT / "tools" / "telegram_bot.py"), "--delay", "3"], cwd=str(ROOT),
                         creationflags=_NO_WINDOW)
        os._exit(0)

    def check_updates(self):
        """Раз на 6 годин: якщо на GitHub нова версія — повідомити (один раз на версію)."""
        if time.time() - self.last_update_check < 6 * 3600 or self.owner is None:
            return
        self.last_update_check = time.time()
        import update
        try:
            lv, rv = update.local_version(), update.remote_version()
        except Exception:
            return
        if lv.get("sha") and lv["sha"] != rv["sha"] and self.notified_sha != rv["sha"]:
            self.notified_sha = rv["sha"]
            self.cfg["notified_sha"] = rv["sha"]
            save_cfg(self.cfg)
            self.send(f"🆕 Вийшло оновлення: {rv['message']}", self.UPDATE_BTN)

    HELP = ("Команди:\n/status — що зараз обробляється\n/shorts — шортси останнього результату\n"
            "/all — усі результати\n/kept — шортси в папці публікації\n/update — оновлення з GitHub\n\n"
            "Під шортсом: ▶ 480p — легкий перегляд, 📱 оригінал (якщо до 50 МБ), "
            "✅ Підходить — перенести MP4 (і субтитри) в папку публікації, ↩ Повернути — назад, "
            "🗑 Видалити — у output\\_видалені.")

    # -------------------------------------------------------------- кнопки
    TOASTS = {"k": "✅ Підходить — перенесено в папку публікації", "u": "↩ Повернуто з папки публікації",
              "D": "🗑 Видалено", "n": "✅ Цей шортс уже в папці публікації", "c": "Скасовано",
              "v": "Готую відео…", "f": "Надсилаю оригінал…"}

    def _answer(self, cq: dict, text: str | None):
        try:
            self.api.call("answerCallbackQuery", callback_query_id=cq["id"], text=text, show_alert=False)
        except Exception:
            pass

    def on_callback(self, cq: dict):
        """Спливаюче повідомлення-підтвердження на кожну кнопку; довгі дії (відео, оновлення) — одразу."""
        act = cq.get("data", "").partition("|")[0]
        if act in ("v", "f", "U", "j"):
            self._answer(cq, self.TOASTS.get(act))
            self._on_callback(cq)
            return
        try:
            self._on_callback(cq)
        except Exception as e:
            self._answer(cq, f"Помилка: {e}"[:190])
            raise
        self._answer(cq, self.TOASTS.get(act))

    def _on_callback(self, cq: dict):
        data = cq.get("data", "")
        act, _, key = data.partition("|")
        if act == "n":
            return
        if act == "j":
            self.show_job(output_dir(self.settings) / f"job_{key}")
            return
        if act == "U":
            self.do_update()
            return
        found = find_short(self.settings, key)
        if not found:
            self.send("Цього шортсу вже немає.")
            return
        job_dir, s = found
        marks = Marks(self.settings)
        cur, pub = self.locate(job_dir, s, marks)
        if cur is None:
            self.send(f"«{s['name']}» уже видалено або перенесено вручну.")
            return
        msg = cq.get("message") or {}

        is_video = bool(msg.get("video") or msg.get("caption") is not None and msg.get("text") is None)

        def redraw(text: str | None = None, markup: dict | None = None):
            if not msg:
                return
            if text is not None and msg.get("text") is not None:
                self.api.call("editMessageText", chat_id=self.owner, message_id=msg["message_id"],
                              text=text, reply_markup=markup)
            elif text is not None and is_video:
                self.api.call("editMessageCaption", chat_id=self.owner, message_id=msg["message_id"],
                              caption=text, reply_markup=markup)
            else:
                self.api.call("editMessageReplyMarkup", chat_id=self.owner, message_id=msg["message_id"],
                              reply_markup=markup)

        vid = msg.get("text") is None          # кнопки під відео (не під текстом)
        if act in ("v", "f"):
            self.api.call("sendChatAction", chat_id=self.owner, action="upload_video")
            path = make_preview(self.settings, cur) if act == "v" else cur
            if path.stat().st_size > MAX_UPLOAD:
                self.send("Оригінал більший за 50 МБ — Telegram не дозволяє ботам таке надсилати. "
                          "Дивіться ▶ 480p.")
                return
            w, h = (480, 854) if act == "v" else (1080, 1920)
            self.api.send_file("sendVideo", "video", path, chat_id=self.owner, caption=self.short_text(s, cur, pub),
                               supports_streaming="true", width=w, height=h, duration=int(s["duration"]),
                               reply_markup=self.short_markup(key, pub, video=True))
        elif act == "k" and not pub:
            new = move_with_srt(cur, publish_dir(self.settings, self.cfg))
            marks.set(key, new)
            redraw(self.short_text(s, new, True), self.short_markup(key, True, vid))
        elif act == "u" and pub:
            back = move_with_srt(cur, s["mp4"].parent)
            if back != s["mp4"]:
                shutil.move(str(back), str(s["mp4"]))
                if back.with_suffix(".srt").exists():
                    shutil.move(str(back.with_suffix(".srt")), str(s["mp4"].with_suffix(".srt")))
            marks.set(key, None)
            redraw(self.short_text(s, s["mp4"], False), self.short_markup(key, False, vid))
        elif act == "d":
            redraw(None, {"inline_keyboard": [[
                {"text": "🗑 Так, видалити", "callback_data": f"D|{key}"},
                {"text": "Скасувати", "callback_data": f"c|{key}"}]]})
        elif act == "D":
            dst = delete_short(self.settings, job_dir, s, cur)
            marks.set(key, None)
            text = (f"🗑🗑🗑 ВИДАЛЕНО 🗑🗑🗑\n━━━━━━━━━━━━━━━━\n{s['name']}\n"
                    f"(перенесено в output\\{DELETED_DIR}\\{job_dir.name} — можна повернути вручну)")
            if msg:
                redraw(text, None)
            else:
                self.send(text)
            log.info("Видалено %s -> %s", s["name"], dst)
        elif act == "c":
            redraw(None, self.short_markup(key, pub, vid))

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
        elif cmd == "/update":
            self.cmd_update()
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
                {"command": "kept", "description": "Шортси в папці публікації"},
                {"command": "update", "description": "Оновлення з GitHub"}])
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
                try:
                    self.check_updates()
                except Exception as e:
                    log.warning("Перевірка оновлень: %s", e)
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
    if "--delay" in sys.argv:                 # перезапуск після оновлення: дати старому процесу завершитись
        time.sleep(float(sys.argv[sys.argv.index("--delay") + 1]))
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
    bot = Bot(settings, cfg)
    bot.lock = lock
    bot.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
