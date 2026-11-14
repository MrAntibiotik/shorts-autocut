"""Живі картки реплеїв у Telegram-боті: одне повідомлення на бій, яке оновлюється на кожному етапі —
від скачування до готовності до публікації.

    🎬 Type 2605 · Malinovka · 10 178 шкоди · 5 фр.
    ✅ Скачано
    ✅ Перевірено — версія 2.4.1.1
    ⏳ OBS — запускаю…
    ⏸ Запис бою — чекає
    ⏸ Нарізка — чекає
    ⏸ Передача в Poster — чекає
    ⏸ Публікація — чекає

⏸ чекає · ⏳ в процесі · ✅ готово · ⏭ пропущено · ❌ помилка (з причиною).
Окремими повідомленнями (щоб телефон сповістив): «🎥 Запис почався» і «✅ Готово до публікації» / «❌ …».
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

STAGES = [("dl", "Скачано"), ("check", "Перевірено"), ("obs", "OBS"), ("rec", "Запис бою"),
          ("cut", "Нарізка"), ("post", "Передача в Poster"), ("pub", "Публікація")]
ICON = {"wait": "⏸", "run": "⏳", "done": "✅", "skip": "⏭", "err": "❌"}
EDIT_EVERY_S = 20          # прогрес запису — не частіше (обмеження Telegram на редагування)


def _n(v) -> str:
    return f"{int(v):,}".replace(",", " ")


class Cards:
    def __init__(self, bot):
        self.bot = bot
        self.cards: dict[str, dict] = {}       # ключ — назва файлу реплею без розширення
        self.lock = threading.Lock()

    # ------------------------------------------------------------------ вигляд
    @staticmethod
    def head(info) -> str:
        if info is None:
            return "🎬 Реплей"
        parts = [f"{info.vehicle} · {info.map_name}", f"{_n(info.damage)} шкоди"]
        if info.assist >= 500:
            parts.append(f"{_n(info.assist)} засвіту")
        if info.kills:
            parts.append(f"{info.kills} фр.")
        return "🎬 " + " · ".join(parts)

    def text(self, c: dict) -> str:
        lines = [c["head"]]
        if c.get("queue"):
            lines.append(f"🕓 {c['queue']}")
        for key, name in STAGES:
            state, det = c["st"].get(key, "wait"), c["det"].get(key, "")
            if state == "wait" and not det:
                det = "чекає"
            lines.append(f"{ICON[state]} {name}" + (f" — {det}" if det else ""))
        return "\n".join(lines)

    # ------------------------------------------------------------------ дії
    def create(self, key: str, info, version: str = "", queue: str = "") -> None:
        c = {"head": self.head(info), "st": {"dl": "done", "check": "done"},
             "det": {"check": f"версія {version}" if version else ""}, "queue": queue,
             "msg": None, "edited": 0.0, "info": info}
        stop = {"inline_keyboard": [[{"text": "⏹ Зупинити запис", "callback_data": "F|stop"}]]}
        res = self.bot.send(self.text(c), stop if "вкладк" in queue else None)
        c["msg"] = res.get("message_id") if isinstance(res, dict) else None
        with self.lock:
            self.cards[key] = c
            if len(self.cards) > 60:                     # старі завершені картки більше не оновлюємо
                for k in list(self.cards)[:-60]:
                    self.cards.pop(k, None)

    def _edit(self, c: dict, force: bool) -> None:
        if not c.get("msg"):
            return
        now = time.time()
        if not force and now - c["edited"] < EDIT_EVERY_S:
            return
        c["edited"] = now
        active = c["st"].get("rec") == "run" or c["st"].get("obs") == "run" or "вкладк" in (c.get("queue") or "")
        markup = {"inline_keyboard": [[{"text": "⏹ Зупинити запис", "callback_data": "F|stop"}]]} if active \
            else {"inline_keyboard": []}
        try:
            self.bot.api.call("editMessageText", chat_id=self.bot.owner, message_id=c["msg"], text=self.text(c),
                              reply_markup=markup)
        except Exception as e:  # noqa: BLE001 — «not modified» і подібне не важливі
            if "not modified" not in str(e):
                import logging
                logging.getLogger("replays").debug("Картка: %s", e)

    def set_queue(self, keys: list[str]) -> None:
        """«У черзі: перед ним N» — для тих, хто ще чекає запису."""
        with self.lock:
            for n, key in enumerate(keys):
                c = self.cards.get(key)
                if c and c["st"].get("rec", "wait") == "wait":
                    c["queue"] = f"у черзі на запис, перед ним: {n}" if n else ""
                    self._edit(c, True)

    def stage(self, rp, stage: str, state: str, detail: str = "") -> None:
        """Подія конвеєра (replay_job.run on_stage). rp=None — для всіх активних карток."""
        with self.lock:
            if rp is None:
                targets = [c for c in self.cards.values() if c["st"].get("post") not in ("done", "skip", "err")
                           and "err" not in c["st"].values()]
            else:
                key = Path(rp).stem
                if key not in self.cards:                 # реплей прийшов не через бота (з вкладки)
                    from shorts.replay_meta import parse
                    try:
                        info = parse(rp)
                    except Exception:  # noqa: BLE001
                        info = None
                    self.cards[key] = {"head": self.head(info), "st": {"dl": "done", "check": "done"}, "det": {},
                                       "queue": "", "msg": None, "edited": 0.0, "info": info}
                    res = self.bot.send(self.text(self.cards[key]))
                    self.cards[key]["msg"] = res.get("message_id") if isinstance(res, dict) else None
                targets = [self.cards[key]]
            for c in targets:
                changed = c["st"].get(stage) != state
                c["st"][stage] = state
                c["det"][stage] = detail
                if stage == "rec" and state == "run":
                    c["queue"] = ""
                    if changed:
                        self.bot.send(f"🎥 Запис почався: {c['head'][2:]}")
                if stage == "post" and state == "done":
                    c["st"]["pub"] = "run"
                    c["det"]["pub"] = "Poster EN-каналу поставить у найближчий слот і надішле перед публікацією"
                    self.bot.send(f"✅ Готово до публікації: {c['head'][2:]}")
                if stage in ("cut", "post") and state == "skip" and c["st"].get("pub", "wait") == "wait":
                    c["st"]["pub"] = "skip"
                    c["det"]["pub"] = "немає шортса" if stage == "cut" else "не передано в Poster"
                if state == "err" and rp is not None:    # спільну помилку (OBS, гра) бот пише один раз сам
                    self.bot.send(f"❌ {c['head'][2:]}\n{dict(STAGES).get(stage, stage)}: {detail}")
                self._edit(c, force=changed or state != "run")


__all__ = ["Cards", "STAGES"]
