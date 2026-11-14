"""Реплеї в Telegram-боті автонарізки (кнопка «🎮 Реплеї»).

• Фільтр сайту wot-record.com кнопками: рівень, тип, майстерність, нації, сортування, мінімальна шкода.
  Кнопка «🌐 Відкрити підбірку» відкриває сайт з цим фільтром прямо на телефоні.
• Реплей, скачаний на телефоні, просто перешліть боту файлом (.wotreplay) — бот перевірить його
  (версія гри, бій дограно, шкода/фраги) і покладе в чергу на ПК.
• «🎬 Записати зібрані» — запис через гру й OBS на ПК, нарізка, передача в Poster EN-каналу.
  Або сам, коли нових реплеїв не було N хв (як у вкладці).
Фільтр і налаштування — спільні з вкладкою «🎮 Реплеї» (work/replays_state.json).
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.request
from pathlib import Path

from shorts.replay_filter import build_url, load_filters
from shorts.replay_state import Busy, busy, cta_text, effective, load, save

log = logging.getLogger("replays")

TIERS = ["5", "6", "7", "8", "9", "10", "11"]
TYPES = [("1", "ЛТ"), ("2", "СТ"), ("3", "ТТ"), ("4", "ПТ"), ("5", "САУ")]
MASTERY = [("4", "Ace"), ("3", "I"), ("2", "II"), ("1", "III")]
QUICK = {"min_damage": [0, 3000, 4000, 5000, 6000, 8000], "min_assist": [0, 2000, 3000, 4000, 5000, 7000]}
LIMIT_NAMES = {"min_damage": "шкоду", "min_assist": "засвіт (асист)"}


def parse_number(text: str) -> int | None:
    """«5500», «5 500», «5.5k», «7к» → число (0…30000); інше — None."""
    t = (text or "").strip().lower().replace(" ", "").replace("\u00a0", "")
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)([kк])?", t)
    if not m:
        return None
    num = m.group(1).replace(",", ".")
    if m.group(2):
        v = float(num) * 1000
    elif "." in num:       # «5.500» — тисячі через крапку
        whole, frac = num.split(".")
        if len(frac) != 3:
            return None
        v = float(whole + frac)
    else:
        v = float(num)
    return int(round(v)) if 0 <= v <= 30000 else None


def fmt_n(v: int) -> str:
    return f"{int(v):,}".replace(",", " ")
NATION_SHORT = {"0": "СРСР", "1": "Німеччина", "2": "США", "3": "Китай", "4": "Франція", "5": "Британія",
                "6": "Японія", "7": "Чехословаччина", "8": "Швеція", "9": "Польща", "10": "Італія"}
MAX_FILE = 20 * 1024 * 1024        # Bot API віддає файли до 20 МБ (реплеї — 1–5 МБ)


def _toggle(lst: list, v: str) -> list:
    return [x for x in lst if x != v] if v in lst else lst + [v]


class ReplaysBot:
    def __init__(self, bot):
        self.bot = bot
        self.settings = bot.settings
        self.view = "main"
        self.thread: threading.Thread | None = None
        self.last_received = 0.0
        self.awaiting: str | None = None     # «min_damage» / «min_assist» — чекаємо число від вас

    # -------------------------------------------------------------- стан
    def state(self) -> dict:
        return effective(self.settings)

    def update(self, **changes) -> None:
        save(self.settings, {**load(self.settings), **changes})

    def client_version(self, st: dict) -> tuple:
        from shorts.replay_record import client_version
        return client_version(Path(st["game_dir"]))

    def version(self, st: dict) -> str:
        if str(st.get("version") or "").strip():
            return str(st["version"]).strip()
        ver = self.client_version(st)
        return f"{ver[0]}.{ver[1]}" if len(ver) >= 2 else ""

    def selection(self, st: dict) -> dict:
        sel = {k: list(v) for k, v in (st.get("wr_sel") or {}).items()}
        ver = self.version(st)
        sel["version"] = [ver] if ver else []
        return sel

    def pending(self, st: dict) -> int:
        from shorts.replay_job import pending_replays
        try:
            return len(pending_replays(Path(st["replays_dir"])))
        except OSError:
            return 0

    # -------------------------------------------------------------- меню
    def text(self, st: dict) -> str:
        sel = st.get("wr_sel") or {}
        sorts = {o["value"]: o["label"] for o in load_filters().get("sorts", [])}
        parts = []
        if sel.get("tier"):
            parts.append("рівень " + ", ".join(sorted(sel["tier"], key=int)))
        if sel.get("type"):
            parts.append(" ".join(dict(TYPES).get(t, t) for t in sel["type"]))
        if sel.get("mastery"):
            parts.append("майстерність " + " ".join(dict(MASTERY).get(m, m) for m in sel["mastery"]))
        if sel.get("nation"):
            parts.append(", ".join(NATION_SHORT.get(n, n) for n in sel["nation"]))
        if sel.get("tank"):
            parts.append(f"танків: {len(sel['tank'])}")
        if sel.get("map"):
            parts.append(f"карт: {len(sel['map'])}")
        ver = self.version(st)
        state = ("⏺ Зараз іде запис/нарізка на ПК" if (self.thread and self.thread.is_alive()) or
                 busy(Path(st["replays_dir"])) else f"Зібрано до запису: {self.pending(st)}")
        idle = int(st.get("idle_min") or 0)
        md, ma = int(st.get("min_damage") or 0), int(st.get("min_assist") or 0)
        limits = " або ".join(x for x in ((f"шкода від {fmt_n(md)}" if md else ""),
                                           (f"засвіт від {fmt_n(ma)}" if ma else "")) if x) or "будь-які"
        no_game = "" if ver else (
            "⚠️ Папку гри не знайдено — у вкладці «🎮 Реплеї» на ПК вкажіть папку гри й натисніть «▶ Почати» "
            "(інакше реплеї іншої версії не відсіються).\n")
        return ("🎮 Реплеї для EN-каналу\n\n"
                f"Фільтр: {' · '.join(parts) or 'усі бої'}\n"
                f"Сортування: {sorts.get(st.get('sort') or 'date', st.get('sort'))} · версія: {ver or 'будь-яка'}\n"
                f"Беру бої: {limits}\n"
                f"{no_game}"
                f"{state}\n\n"
                "Як: 🌐 Відкрити підбірку → скачайте реплей → перешліть файл сюди "
                "(або скачайте на ПК — вкладка забере з «Завантажень»). "
                + (f"Запис почнеться сам через {idle} хв без нових реплеїв або кнопкою." if idle else
                   "Запис — кнопкою «🎬 Записати зібрані»."))

    def markup(self, st: dict) -> dict:
        sel = st.get("wr_sel") or {}

        def b(label, data, on=False):
            return {"text": ("✓" if on else "") + label, "callback_data": f"F|{data}"}  # коротко — щоб не обрізалось

        if self.view == "nation":
            nats = [o["value"] for o in load_filters().get("nation", [])]
            rows, row = [], []
            for n in nats:
                row.append(b(NATION_SHORT.get(n, n), f"n:{n}", n in (sel.get("nation") or [])))
                if len(row) == 3:
                    rows.append(row)
                    row = []
            if row:
                rows.append(row)
            rows.append([b("⬅ Готово", "v:main")])
            return {"inline_keyboard": rows}
        sorts = load_filters().get("sorts", [])
        sort_label = next((o["label"] for o in sorts if o["value"] == st.get("sort")), "Нові")
        n_nat = len(sel.get("nation") or [])
        md, ma = int(st.get("min_damage") or 0), int(st.get("min_assist") or 0)
        rows = [
            [b(t, f"t:{t}", t in (sel.get("tier") or [])) for t in TIERS],
            [b(lbl, f"y:{v}", v in (sel.get("type") or [])) for v, lbl in TYPES],
            [b(lbl, f"m:{v}", v in (sel.get("mastery") or [])) for v, lbl in MASTERY],
            [b(f"🌍 Нації{f' ({n_nat})' if n_nat else ''}", "v:nation"), b(f"↕ {sort_label}", "s")],
            [b(f"💥 Шкода від {fmt_n(md) if md else '—'}", "ask:min_damage"),
             b(f"🔦 Засвіт від {fmt_n(ma) if ma else '—'}", "ask:min_assist")],
            [{"text": "🌐 Відкрити підбірку", "url": build_url(self.selection(st), sort=st.get("sort") or "date")}],
            [b(f"🎬 Записати зібрані ({self.pending(st)})", "go"), b("🔄", "ref"), b("♻ Скинути", "r")],
        ]
        return {"inline_keyboard": rows}

    def show(self):
        st = self.state()
        self.view = "main"
        self.bot.send(self.text(st), self.markup(st))

    def redraw(self, msg: dict):
        st = self.state()
        try:
            self.bot.api.call("editMessageText", chat_id=self.bot.owner, message_id=msg["message_id"],
                              text=self.text(st), reply_markup=self.markup(st), disable_web_page_preview=True)
        except RuntimeError as e:
            if "not modified" not in str(e):
                raise

    def on_callback(self, cq: dict, key: str) -> str | None:
        """Кнопки фільтра. Повертає текст спливаючого повідомлення."""
        st = self.state()
        sel = {k: list(v) for k, v in (st.get("wr_sel") or {}).items()}
        msg = cq.get("message") or {}
        kind, _, val = key.partition(":")
        toast = None
        if kind in ("t", "y", "m", "n"):
            field = {"t": "tier", "y": "type", "m": "mastery", "n": "nation"}[kind]
            sel[field] = _toggle(sel.get(field) or [], val)
            if field in ("tier", "type", "nation"):
                sel.pop("tank", None)          # вибрані у вкладці танки могли вже не підходити
            self.update(wr_sel=sel)
        elif kind == "v":
            self.view = val or "main"
        elif kind == "s":
            sorts = [o["value"] for o in load_filters().get("sorts", [])] or ["date"]
            cur = st.get("sort") or "date"
            self.update(sort=sorts[(sorts.index(cur) + 1) % len(sorts)] if cur in sorts else sorts[0])
        elif kind == "ask" and val in QUICK:
            self.awaiting = val
            cur = int(st.get(val) or 0)
            quick = [{"text": ("✓" if v == cur else "") + (fmt_n(v) if v else "без порогу"),
                      "callback_data": f"F|set:{val}={v}"} for v in QUICK[val]]
            self.bot.send(f"✍️ Напишіть мінімальну {LIMIT_NAMES[val]} числом — напр. 5500 або 5.5k "
                          f"(зараз: {fmt_n(cur) if cur else 'без порогу'}). Або виберіть:",
                          {"inline_keyboard": [quick[:3], quick[3:]]})
            return None
        elif kind == "set":
            field, _, num = val.partition("=")
            if field in QUICK and num.isdigit():
                self.update(**{field: int(num)})
                self.awaiting = None
                if msg and "Напишіть мінімальну" in (msg.get("text") or ""):
                    self.bot.delete_msg(msg.get("message_id"))     # підказку прибираємо, показуємо меню знову
                    self.show()
                    return f"{LIMIT_NAMES[field].capitalize()} від {fmt_n(int(num))}" if int(num) else "Без порогу"
        elif kind == "r":
            self.update(wr_sel={}, sort="date", min_damage=0, min_assist=0)
            toast = "♻ Фільтр скинуто"
        elif kind == "go":
            toast = self.start(manual=True)
        if msg:
            self.redraw(msg)
        return toast

    def on_text(self, text: str) -> bool:
        """Число після «💥 Шкода від» / «🔦 Засвіт від». True — повідомлення оброблено."""
        if not self.awaiting:
            return False
        v = parse_number(text)
        if v is None:
            self.bot.send("Не схоже на число. Напишіть, напр., 5500 або 5.5k (0 — без порогу).")
            return True
        field, self.awaiting = self.awaiting, None
        self.update(**{field: v})
        self.show()
        return True

    # -------------------------------------------------------------- файли реплеїв
    def on_document(self, m: dict) -> None:
        doc = m.get("document") or {}
        name = str(doc.get("file_name") or "replay.wotreplay")
        if not name.lower().endswith(".wotreplay"):
            self.bot.send("Це не реплей. Надішліть файл .wotreplay (скачаний з wot-record.com).")
            return
        if int(doc.get("file_size") or 0) > MAX_FILE:
            self.bot.send("Файл завеликий для Telegram-бота (понад 20 МБ). Скачайте його на ПК — "
                          "вкладка «🎮 Реплеї» забере з «Завантажень».")
            return
        st = self.state()
        replays_dir = Path(st["replays_dir"])
        replays_dir.mkdir(parents=True, exist_ok=True)
        info_ = self.bot.api.call("getFile", file_id=doc["file_id"])
        url = self.bot.api.base.replace("/bot", "/file/bot", 1) + info_["file_path"]
        with urllib.request.urlopen(url, timeout=120) as r:  # noqa: S310 — адреса Telegram
            data = r.read()
        stem = re.sub(r"[^\w\-]+", "_", Path(name).stem)[:80] or f"tg_{int(time.time())}"
        target = replays_dir / f"{stem}.wotreplay"
        if target.exists() or target.with_suffix(".recorded").exists():
            self.bot.send(f"«{name}» уже є в черзі або записаний.")
            return
        tmp = replays_dir / f"{stem}.part"
        tmp.write_bytes(data)
        from shorts.replay_download import ImportOptions, check_replay
        opts = ImportOptions(src=replays_dir, dest=replays_dir, client_version=self.client_version(st)[:3],
                             min_damage=int(st.get("min_damage") or 0), min_kills=int(st.get("min_kills") or 0),
                             min_assist=int(st.get("min_assist") or 0))
        info, why = check_replay(tmp, opts)
        if why:
            tmp.unlink(missing_ok=True)
            self.bot.send(f"✖ {name}: не беру — {why}.")
            return
        tmp.replace(target)
        info.path = str(target)
        target.with_suffix(".json").write_text(json.dumps(info.to_dict(), ensure_ascii=False, indent=1),
                                               encoding="utf-8")
        self.last_received = time.time()
        idle = int(st.get("idle_min") or 0)
        n = self.pending(st)
        self.bot.send(f"✓ {info.vehicle} · {info.map_name} · {info.damage:,} шкоди · фрагів {info.kills}\n"
                      f"У черзі на запис: {n}."
                      + (f" Запис почнеться сам через {idle} хв без нових реплеїв." if idle else ""),
                      {"inline_keyboard": [[{"text": f"🎬 Записати зібрані ({n})", "callback_data": "F|go"}]]})

    # -------------------------------------------------------------- запис і нарізка
    def tick(self) -> None:
        """Автозапуск: реплеї прийшли через бота і нових не було idle_min хвилин."""
        if not self.last_received:
            return
        st = self.state()
        idle = int(st.get("idle_min") or 0)
        if idle and time.time() - self.last_received >= idle * 60:
            self.last_received = 0.0
            self.start(manual=False)

    def start(self, manual: bool) -> str:
        st = self.state()
        replays_dir = Path(st["replays_dir"])
        if (self.thread and self.thread.is_alive()) or busy(replays_dir):
            return "⏺ Запис уже йде"
        n = self.pending(st)
        if not n:
            return "Черга порожня — спершу надішліть реплеї"
        self.thread = threading.Thread(target=self._work, args=(st,), daemon=True, name="replays-bot")
        self.thread.start()
        return f"🎬 Починаю запис: {n}"

    def _work(self, st: dict) -> None:
        from shorts.replay_job import ReplayJobOptions, run
        from shorts.replay_meta import title_en
        from shorts.replay_record import RecordError, RecordOptions
        n = self.pending(st)
        self.bot.send(f"🎬 Почав запис {n} реплеїв на ПК: гра відкриється на весь екран, OBS записує. "
                      "Готові шортси підуть у Poster EN-каналу, сюди — коротко про кожен.")
        ropts = RecordOptions(game_dir=Path(st["game_dir"]), obs_host=st["obs_host"], obs_port=int(st["obs_port"]),
                              obs_password=st["obs_password"], load_s=float(st["load_s"]))
        jopts = ReplayJobOptions(replays_dir=Path(st["replays_dir"]),
                                 publish_dir=Path(st["publish_dir"]) if str(st.get("publish_dir") or "").strip() else None,
                                 cta_text=cta_text(st), credit_author=bool(st.get("credit", True)),
                                 record=bool(st.get("do_record", True)), cut=bool(st.get("do_cut", True)))

        def on_short(path, info):
            self.bot.send(f"✅ Шортс: {title_en(info) if info else Path(path).stem}"
                          + ("\n→ у Poster EN-каналу" if jopts.publish_dir else "\n(папку EN-каналу не вказано — лише output)"))
        try:
            summary = run(self.settings, jopts, ropts, who="Telegram-бот", on_short=on_short)
        except Busy as e:
            self.bot.send(f"⏺ Не починаю: {e}.")
            return
        except RecordError as e:
            self.bot.send(f"❌ Запис не почався: {e}")
            return
        except Exception as e:  # noqa: BLE001
            log.exception("Реплеї з бота")
            self.bot.send(f"❌ Помилка: {type(e).__name__}: {e}")
            return
        lines = [f"🏁 Готово: записано {summary['recorded']}, шортсів {len(summary['shorts'])}, "
                 f"у Poster {len(summary['published'])}."]
        lines += [f"❌ {e}" for e in summary["errors"][:5]]
        self.bot.send("\n".join(lines))


__all__ = ["ReplaysBot"]
