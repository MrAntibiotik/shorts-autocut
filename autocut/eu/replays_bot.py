"""Реплеї в Telegram-боті автонарізки (кнопка «🇬🇧 EU: реплеї»).

• Фільтр сайту wot-record.com кнопками: рівень, тип, майстерність, нації, сортування, мінімальна шкода.
  Кнопка «🌐 Відкрити підбірку» відкриває сайт з цим фільтром прямо на телефоні.
• Реплей, скачаний на телефоні, просто перешліть боту файлом (.wotreplay) — бот перевірить його
  (версія гри, бій дограно, шкода/фраги) і покладе в чергу на ПК.
• «🎬 Записати зібрані» — запис через гру й OBS на ПК, нарізка, передача в Poster EN-каналу.
  Або сам, коли нових реплеїв не було N хв (як у вкладці).
Фільтр і налаштування — спільні з вкладкою «🇬🇧 EU: реплеї» (work/replays_state.json).
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.request
from pathlib import Path

from autocut.eu.replay_filter import build_url, load_filters
from autocut.eu.replay_state import Busy, busy, cta_text, effective, load, save

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
        self.cancel = threading.Event()      # «⏹ Зупинити запис»
        self.hold_until = 0.0                # після помилки — пауза автозапуску черги (щоб не засипати помилками)
        self.user_stopped = False            # натиснули «⏹» — автозапуск лише після «🎬» або нового реплею
        self.listing: dict = {}              # останній список боїв: {"page", "items", "url", "hidden"}
        self.got: set[str] = set()           # номери реплеїв, скачаних зі списку (перевіряється за файлами)
        from autocut.eu.replay_cards import Cards
        self.cards = Cards(bot)              # живі картки: етапи від скачування до публікації

    # -------------------------------------------------------------- стан
    def state(self) -> dict:
        return effective(self.settings)

    def update(self, **changes) -> None:
        save(self.settings, {**load(self.settings), **changes})

    def client_version(self, st: dict) -> tuple:
        from autocut.eu.replay_record import client_version
        return client_version(Path(st["game_dir"]))

    def version_info(self, st: dict) -> tuple[str, tuple, str]:
        """(«2.4», кортеж для перевірки, звідки): вибрана → ваша гра → найновіша."""
        from autocut.eu.replay_filter import effective_version
        # за замовчуванням — завжди найновіша версія з сайту (версія гри — лише для попередження)
        return effective_version(st.get("version") or "", (), replays_dir=st["replays_dir"])

    def version(self, st: dict) -> str:
        return self.version_info(st)[0]

    def selection(self, st: dict) -> dict:
        from autocut.eu.replay_filter import latest_version, site_knows
        sel = {k: list(v) for k, v in (st.get("wr_sel") or {}).items()}
        ver = self.version(st)
        if ver and not site_knows(ver) and latest_version(replays_dir=st["replays_dir"]) not in ("", ver):
            ver = ""          # версії немає на сайті — без неї в адресі (інакше сайт падає з помилкою 500)
        sel["version"] = [ver] if ver else []
        return sel

    def pending(self, st: dict) -> int:
        from autocut.eu.replay_job import pending_replays
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
        from autocut.eu.replay_filter import latest_version, site_knows
        src = self.version_info(st)[2]
        newest = latest_version(replays_dir=st["replays_dir"])
        no_game = ""
        cv = self.client_version(st)
        game_ver = f"{cv[0]}.{cv[1]}" if len(cv) >= 2 else ""
        if game_ver and ver and game_ver != ver:
            no_game += (f"⚠️ Ваша гра — {game_ver}, а реплеї — {ver}: реплеї іншої версії гра не відкриває. "
                        "Оновіть гру (або виберіть свою версію у вкладці «🇬🇧 EU: реплеї»).\n")
        if ver and src != "найновіша" and not site_knows(ver) and newest and newest != ver:
            no_game += (f"⚠️ {'Ваша гра' if src == 'ваша гра' else 'Вибрана версія'} — {ver}, а на сайті такої версії "
                        f"немає (найновіша там {newest}). Список показую без фільтра версії; реплеї з сайту у вашій грі "
                        "можуть не відкритися — бот відхилятиме їх як «інша версія гри».\n")
        return ("🇬🇧 EU-канал · шортси з реплеїв (у твій канал не потрапляють)\n\n"
                f"Фільтр: {' · '.join(parts) or 'усі бої'}\n"
                f"Сортування: {sorts.get(st.get('sort') or 'date', st.get('sort'))} · версія: "
                f"{ver + ' (' + self.version_info(st)[2] + ')' if ver else 'будь-яка'}\n"
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
            [b("📋 Бої за фільтром", "list:1"),
             {"text": "🌐 Відкрити на сайті", "url": build_url(self.selection(st), sort=st.get("sort") or "date")}],
            [b(f"🎬 EU: записати й нарізати зібрані ({self.pending(st)})", "go")],
            [b("🔄 Оновити цей екран", "ref"), b("♻ Скинути фільтр", "r")],
        ]
        return {"inline_keyboard": rows}

    def show(self):
        st = self.state()
        self.view = "main"
        self.bot.send(self.text(st), self.markup(st))
        if not busy(Path(st["replays_dir"])):          # під час запису «доганяє» сам цикл запису
            from autocut.eu.replay_job import ReplayJobOptions
            jopts = ReplayJobOptions(replays_dir=Path(st["replays_dir"]),
                                     publish_dir=Path(st["publish_dir"]) if str(st.get("publish_dir") or "").strip()
                                     else None, cta_text=cta_text(st), credit_author=bool(st.get("credit", True)))
            threading.Thread(target=self.catch_up, args=(jopts,), daemon=True, name="replays-catch-up").start()

    def redraw(self, msg: dict):
        if hasattr(self.bot, "chat"):
            self.bot.chat.touch(msg.get("message_id"))          # з екраном реплеїв працюють — не прибирати
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
        elif kind == "list":
            return self.show_list(int(val or 1), msg if "📋 Бої" in (msg.get("text") or "") else None)
        elif kind == "dl":
            return self.download(val, msg)
        elif kind == "r":
            self.update(wr_sel={}, sort="date", min_damage=0, min_assist=0)
            toast = "♻ Фільтр скинуто"
        elif kind == "go":
            toast = self.start(manual=True)
        elif kind == "stop":
            return self.stop()
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
                          "вкладка «🇬🇧 EU: реплеї» забере з «Завантажень».")
            return
        info_ = self.bot.api.call("getFile", file_id=doc["file_id"])
        url = self.bot.api.base.replace("/bot", "/file/bot", 1) + info_["file_path"]
        with urllib.request.urlopen(url, timeout=120) as r:  # noqa: S310 — адреса Telegram
            data = r.read()
        self.accept(name, data)

    def accept(self, name: str, data: bytes) -> bool:
        """Перевіряє реплей (версія, дограно, пороги) і кладе в чергу. True — прийнято."""
        st = self.state()
        replays_dir = Path(st["replays_dir"])
        replays_dir.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r"[^\w\-]+", "_", Path(name).stem)[:80] or f"tg_{int(time.time())}"
        target = replays_dir / f"{stem}.wotreplay"
        if target.with_suffix(".failed").exists() and target.exists():   # раніше не вдався — ще раз
            target.with_suffix(".failed").unlink(missing_ok=True)
            self.bot.send(f"🔁 «{name}» раніше не записався — ставлю в чергу ще раз.")
            if st.get("instant", True) and not ((self.thread and self.thread.is_alive()) or busy(replays_dir)):
                self.start(manual=False)
            return True
        if not target.exists():                 # реплей видалили — службові залишки теж прибрати
            for ext in (".recorded", ".failed", ".json", ".part"):
                target.with_suffix(ext).unlink(missing_ok=True)
        if target.exists():
            self.bot.send(f"«{name}» уже є в черзі або записаний.")
            return True
        tmp = replays_dir / f"{stem}.part"
        tmp.write_bytes(data)
        from autocut.eu.replay_download import ImportOptions, check_replay
        opts = ImportOptions(src=replays_dir, dest=replays_dir, client_version=self.version_info(st)[1],
                             min_damage=int(st.get("min_damage") or 0), min_kills=int(st.get("min_kills") or 0),
                             min_assist=int(st.get("min_assist") or 0))
        info, why = check_replay(tmp, opts)
        if why:
            tmp.unlink(missing_ok=True)
            self.bot.send(f"✖ {name}: не беру — {why}.")
            return False
        tmp.replace(target)
        info.path = str(target)
        target.with_suffix(".json").write_text(json.dumps(info.to_dict(), ensure_ascii=False, indent=1),
                                               encoding="utf-8")
        mine = bool(self.thread and self.thread.is_alive())
        foreign = not mine and busy(replays_dir, own_running=False)    # залишки обірваного запису знімаються
        running = mine or foreign
        ahead = self.pending(st) - 1
        queue = ("чекає: запис іде з вкладки «🇬🇧 EU: реплеї» на ПК — почнеться, щойно та закінчить "
                 "(або «⏹ Зупинити запис»)" if foreign else
                 f"у черзі на запис, перед ним: {ahead}" if running and ahead > 0 else
                 "чекає, доки закінчиться запис іншого бою" if running else "")
        self.user_stopped = False            # новий реплей — знову можна автоматично
        self.cards.create(stem, info, version=info.version, queue=queue)   # жива картка етапів
        if st.get("instant", True):        # одразу: OBS → запис → нарізка → Poster
            if not running:
                self.start(manual=False)
            return True
        self.last_received = time.time()
        idle = int(st.get("idle_min") or 0)
        n = self.pending(st)
        self.bot.send(f"У черзі на запис: {n}."
                      + (f" Запис почнеться сам через {idle} хв без нових реплеїв." if idle else ""),
                      {"inline_keyboard": [[{"text": f"🎬 Записати зібрані ({n})", "callback_data": "F|go"}]]})
        return True

    # -------------------------------------------------------------- список боїв з сайту (на ваше натискання)
    def passes(self, it: dict, st: dict) -> bool:
        """Пороги «шкода від X або засвіт від Y» — ті самі, що для файлів."""
        md, ma = int(st.get("min_damage") or 0), int(st.get("min_assist") or 0)
        if ma > 0:
            return (md > 0 and it["damage"] >= md) or it["assist"] >= ma
        return it["damage"] >= md

    def list_url(self, st: dict) -> str:
        return build_url(self.selection(st), sort=st.get("sort") or "date")

    def has(self, rid: str) -> bool:
        """Чи є реплей у папці реплеїв зараз (видалили файл — можна скачати знову)."""
        d = Path(self.state()["replays_dir"])
        found = any(d.glob(f"wr_{rid}_*.wotreplay")) if d.is_dir() else False
        if not found:
            self.got.discard(rid)
        return found

    def list_text(self) -> str:
        L = self.listing
        from autocut.eu.replay_site import line
        lines = [f"📋 Бої з wot-record.com — сторінка {L['page']}"]
        if L["hidden"]:
            lines.append(f"(сховано слабших за пороги: {L['hidden']})")
        lines.append("")
        for n, it in enumerate(L["items"], 1):
            mark = "✅" if self.has(it["id"]) else f"{n}."
            title = f" — «{it['title'][:40]}»" if it["title"] else ""
            lines.append(f"{mark} {line(it)}{title}")
        if not L["items"]:
            lines.append("На цій сторінці немає боїв під ваші пороги — гляньте «➡ Далі».")
        lines += ["", "⬇ N — скачати бій N і поставити в чергу на запис."]
        return "\n".join(lines)

    def list_markup(self) -> dict:
        L = self.listing
        btns = [{"text": ("✅" if self.has(it["id"]) else "⬇") + str(n), "callback_data": f"F|dl:{it['id']}"}
                for n, it in enumerate(L["items"], 1)]
        rows = [btns[i:i + 5] for i in range(0, len(btns), 5)]
        nav = []
        if L["page"] > 1:
            nav.append({"text": "⬅ Назад", "callback_data": f"F|list:{L['page'] - 1}"})
        nav.append({"text": "➡ Далі", "callback_data": f"F|list:{L['page'] + 1}"})
        rows.append(nav)
        st = self.state()
        rows.append([{"text": f"🎬 Записати зібрані ({self.pending(st)})", "callback_data": "F|go"},
                     {"text": "🌐 На сайті", "url": self.list_url(st)}])
        return {"inline_keyboard": rows}

    def show_list(self, page: int, msg: dict | None) -> str | None:
        """Одна сторінка списку з сайту — лише на ваше натискання (один запит)."""
        from autocut.eu.replay_site import SiteError, fetch_list
        st = self.state()
        url = self.list_url(st)
        try:
            items = fetch_list(url, page)
        except SiteError as e:
            self.bot.send(f"❌ Список не завантажився: {e}.")
            return "Не вдалося"
        good = [it for it in items if self.passes(it, st)]
        self.listing = {"page": page, "items": good, "url": url, "hidden": len(items) - len(good)}
        if msg:
            try:
                self.bot.api.call("editMessageText", chat_id=self.bot.owner, message_id=msg["message_id"],
                                  text=self.list_text(), reply_markup=self.list_markup(),
                                  disable_web_page_preview=True)
                return f"Сторінка {page}"
            except RuntimeError as e:
                if "not modified" in str(e):
                    return None
        self.bot.send(self.list_text(), self.list_markup())
        return f"Сторінка {page}"

    def download(self, rid: str, msg: dict) -> str:
        """Один реплей зі списку (один запит) → перевірка → черга на запис."""
        from autocut.eu.replay_site import SiteError, download
        if self.has(rid):
            return "✅ Уже скачано"
        if not rid.isdigit():
            return "?"
        it = next((x for x in self.listing.get("items", []) if x["id"] == rid), {})
        site = re.match(r"https?://[^/]+", self.listing.get("url") or self.list_url(self.state())).group(0)
        try:
            data = download(site, rid, referer=f"{site}/replay/{rid}/{it.get('slug', '')}")
        except SiteError as e:
            self.bot.send(f"❌ #{rid}: {e}.")
            return "Не вдалося"
        name = f"wr_{rid}_{it.get('tank', '')}_{it.get('map', '')}.wotreplay"
        if self.accept(name, data):
            self.got.add(rid)
        if msg and self.listing:
            try:
                self.bot.api.call("editMessageText", chat_id=self.bot.owner, message_id=msg["message_id"],
                                  text=self.list_text(), reply_markup=self.list_markup(),
                                  disable_web_page_preview=True)
            except RuntimeError as e:
                if "not modified" not in str(e):
                    log.warning("Список: %s", e)
        return "⬇ Скачано" if rid in self.got else "✖ Не підходить"

    # -------------------------------------------------------------- запис і нарізка
    def tick(self) -> None:
        """Раз на ~10 с: (1) у черзі є реплеї, запис вільний — починаємо (режим «одразу»);
        (2) режим очікування: реплеї прийшли через бота і нових не було idle_min хвилин."""
        st = self.state()
        if (st.get("instant", True) and not self.user_stopped and time.time() >= self.hold_until
                and not (self.thread and self.thread.is_alive()) and self.pending(st)
                and not busy(Path(st["replays_dir"]), own_running=False)):
            self.start(manual=False)
            return
        if not self.last_received:
            return
        idle = int(st.get("idle_min") or 0)
        if idle and time.time() - self.last_received >= idle * 60:
            self.last_received = 0.0
            self.start(manual=False)

    def start(self, manual: bool) -> str:
        st = self.state()
        replays_dir = Path(st["replays_dir"])
        mine = bool(self.thread and self.thread.is_alive())
        if manual:
            self.user_stopped = False
            self.hold_until = 0.0
        if mine or busy(replays_dir, own_running=False):     # обірвані залишки busy() знімає сам
            if manual:
                self.bot.send("⏺ Запис уже йде" + ("" if mine else " (з вкладки на ПК)") + ". Зупинити?",
                              {"inline_keyboard": [[{"text": "⏹ Зупинити запис", "callback_data": "F|stop"}]]})
            return "⏺ Запис уже йде"
        self.cancel.clear()
        if manual:                        # «🎬 Записати зібрані» — і ті, що раніше не вдалися
            from autocut.eu.replay_job import retry_failed
            retry_failed(replays_dir)
        n = self.pending(st)
        if not n:
            return "Черга порожня — спершу надішліть реплеї"
        self.thread = threading.Thread(target=self._work, args=(st,), daemon=True, name="replays-bot")
        self.thread.start()
        return f"🎬 Починаю запис: {n}"

    def stop(self) -> str:
        """«⏹ Зупинити запис»: OBS зупиняється, гра закривається, позначка «іде запис» знімається."""
        from autocut.eu.replay_record import kill_game
        from autocut.eu.replay_state import clear_lock
        st = self.state()
        self.cancel.set()
        self.user_stopped = True             # сам не перезапускаю — чекаю «🎬 Записати зібрані»
        running = bool(self.thread and self.thread.is_alive())
        if not running:                      # запис ішов не з бота або обірвався — прибираємо самі
            kill_game()
            clear_lock(Path(st["replays_dir"]))
            self.bot.send("⏹ Позначку «іде запис» знято, гру закрито. Можна знову «🎬 Записати зібрані».")
            return "⏹ Зупинено"
        return "⏹ Зупиняю… (до кількох секунд)"

    def catch_up(self, jopts) -> int:
        """Шортси з реплеїв, що змонтувались, але не потрапили в чергу EN-каналу Poster, — віддати зараз."""
        try:
            from autocut.eu.replay_job import publish_unpublished
            caught = publish_unpublished(self.settings, jopts)
        except Exception as e:  # noqa: BLE001
            self.bot.send(f"⚠️ Шортси з реплеїв не віддано в EN-канал: {e}")
            return 0
        if caught:
            self.bot.send(f"🇬🇧 EU: у чергу EU-каналу Poster віддано {len(caught)} шортсів з реплеїв, змонтованих раніше "
                          "(тоді папку EU-каналу ще не було видно). У твій канал вони не потрапляють.")
        return len(caught)

    def _work(self, st: dict) -> None:
        from autocut.eu.replay_job import ReplayJobOptions, run
        from autocut.eu.replay_record import RecordError, RecordOptions
        n = self.pending(st)
        if not st.get("instant", True) or n > 1:
            self.bot.send(f"🎬 Почав запис {n} реплеїв на ПК: гра відкриється на весь екран, OBS записує. "
                          "Готові шортси підуть у Poster EN-каналу, сюди — коротко про кожен.")
        ropts = RecordOptions(game_dir=Path(st["game_dir"]), obs_host=st["obs_host"], obs_port=int(st["obs_port"]),
                              obs_password=st["obs_password"], load_s=float(st["load_s"]),
                              click_at=(str(int(st.get("click_s") or 0)),) if int(st.get("click_s") or 0) else (),
                              obs_exe=str(st.get("obs_exe") or ""), obs_profile=str(st.get("obs_profile") or ""),
                              obs_collection=str(st.get("obs_collection") or ""),
                              record_dir=str(st.get("record_dir") or ""))
        jopts = ReplayJobOptions(replays_dir=Path(st["replays_dir"]),
                                 publish_dir=Path(st["publish_dir"]) if str(st.get("publish_dir") or "").strip() else None,
                                 cta_text=cta_text(st), credit_author=bool(st.get("credit", True)),
                                 record=bool(st.get("do_record", True)), cut=bool(st.get("do_cut", True)))

        total = {"recorded": 0, "shorts": 0, "published": 0, "errors": []}
        self.catch_up(jopts)
        for _ in range(100):               # реплеї, скачані під час запису, — одразу слідом
            if not self.pending(st):
                break
            try:
                from autocut.eu.replay_job import pending_replays
                self.cards.set_queue([p.stem for p in pending_replays(Path(st["replays_dir"]))])
                summary = run(self.settings, jopts, ropts, who="Telegram-бот", on_stage=self.cards.stage,
                              cancel=self.cancel.is_set)
            except Busy as e:
                self.bot.send(f"⏺ Не починаю: {e}.")
                return
            except RecordError as e:
                self.hold_until = time.time() + 600        # автоповтор черги — не раніше ніж за 10 хв
                self.cards.stage(None, "obs", "err", str(e)[:200])
                self.bot.send(f"❌ Запис не почався: {e}\n\nВиправте й натисніть «🎬 Записати зібрані» "
                              "(сам спробую ще раз за 10 хв).")
                return
            except Exception as e:  # noqa: BLE001
                if type(e).__name__ != "Cancelled" and not self.cancel.is_set():
                    self.hold_until = time.time() + 600
                if type(e).__name__ == "Cancelled" or self.cancel.is_set():
                    self.cards.stage(None, "rec", "skip", "зупинено вами — реплей лишився в черзі")
                    self.bot.send("⏹ Запис зупинено. Реплей лишився в черзі — «🎬 Записати зібрані», щоб почати знову.")
                    return
                log.exception("Реплеї з бота")
                self.bot.send(f"❌ Помилка: {type(e).__name__}: {e}")
                return
            total["recorded"] += summary["recorded"]
            total["shorts"] += len(summary["shorts"])
            total["published"] += len(summary["published"])
            total["errors"] += summary["errors"]
            if not summary["recorded"] and not summary["shorts"] and not summary["errors"]:
                break
        lines = [f"🏁 Готово: записано {total['recorded']}, шортсів {total['shorts']}, "
                 f"у Poster {total['published']}."]
        if total["published"]:
            lines.append("Poster EN-каналу поставить їх у розклад і перед публікацією надішле в Telegram.")
        lines += [f"❌ {e}" for e in total["errors"][:5]]
        self.bot.send("\n".join(lines))


__all__ = ["ReplaysBot"]
