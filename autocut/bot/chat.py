"""Чистий чат бота: що лишається, а що прибирається.

У чаті постійно лише:
  • картка статусу (одна, внизу; оновлюється на місці);
  • непідтверджені шортси (відео з кнопками) — після «✅ Підходить» чи «🗑» зникають.
Усе інше (відповіді, підказки, помилки, списки з кнопками, ваші команди) — тимчасове й видаляється саме.

Telegram дозволяє ботам видаляти повідомлення лише за останні 48 год. Тому постійні повідомлення бот
переотправляє, коли їм ~46 год (відео — за file_id, без повторного завантаження): старих «невидалюваних»
повідомлень у чаті не лишається.

Реєстр зберігається у work\\tg_chat.json — переживає перезапуск бота.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

TTL_INFO = 120            # звичайна відповідь / підказка
TTL_BUTTONS = 15 * 60     # повідомлення з кнопками (списки, підтвердження, вибір)
TTL_ERROR = 10 * 60       # помилка — щоб устигнути прочитати
REFRESH_AFTER = 46 * 3600  # постійні повідомлення — переотправити до межі Telegram у 48 год
DELETE_LIMIT = 47.5 * 3600


class ChatRegistry:
    """msgs: {id: {"t": коли надіслано, "until": коли видалити | None — постійне, "kind": ...}}
    shorts: {ключ шортсу: {"id", "t", "file_id"}}; card: {"id", "t"}; max_id — найбільший номер у чаті."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.RLock()
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(self.data, dict):
                raise ValueError
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("msgs", {})
        self.data.setdefault("shorts", {})
        self.data.setdefault("card", None)
        self.data.setdefault("max_id", 0)

    def save(self) -> None:
        with self.lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)

    # ---------------------------------------------------------------- облік
    def add(self, mid, ttl: float | None, kind: str = "info") -> None:
        """ttl=None — постійне (картка, шортс); інакше видалити через ttl секунд."""
        if not mid:
            return
        now = time.time()
        with self.lock:
            self.data["msgs"][str(int(mid))] = {"t": now, "until": None if ttl is None else now + ttl, "kind": kind}
            self.data["max_id"] = max(int(self.data.get("max_id") or 0), int(mid))
            self.save()

    def touch(self, mid, ttl: float = TTL_BUTTONS) -> None:
        """Повідомлення ще потрібне (з ним щойно працювали) — продовжити життя."""
        with self.lock:
            m = self.data["msgs"].get(str(mid))
            if m and m.get("until") is not None:
                m["until"] = max(m["until"], time.time() + ttl)
                self.save()

    def expire(self, mid, ttl: float = TTL_INFO) -> None:
        """Постійне стало тимчасовим (напр. картка запису реплею завершилась)."""
        with self.lock:
            m = self.data["msgs"].get(str(mid))
            if m is not None:
                m["until"] = time.time() + ttl
                self.save()

    def forget(self, mid) -> None:
        with self.lock:
            self.data["msgs"].pop(str(mid), None)
            for k, v in list(self.data["shorts"].items()):
                if str(v.get("id")) == str(mid):
                    self.data["shorts"].pop(k)
            if self.data.get("card") and str(self.data["card"].get("id")) == str(mid):
                self.data["card"] = None
            self.save()

    def due(self, now: float | None = None) -> list[int]:
        """Тимчасові, яким час зникнути (і всі, що підходять до межі 48 год, — поки ще можна видалити)."""
        now = now or time.time()
        out = []
        with self.lock:
            for mid, m in self.data["msgs"].items():
                until = m.get("until")
                if until is not None and (now >= until or now - m["t"] >= DELETE_LIMIT - 600):
                    out.append(int(mid))
        return out

    # ---------------------------------------------------------------- постійне
    def keep_ids(self) -> set[int]:
        with self.lock:
            ids = {int(v["id"]) for v in self.data["shorts"].values() if v.get("id")}
            if self.data.get("card"):
                ids.add(int(self.data["card"]["id"]))
            ids |= {int(mid) for mid, m in self.data["msgs"].items() if m.get("until") is None}
            return ids

    def set_short(self, key: str, mid, file_id: str = "") -> None:
        with self.lock:
            self.data["shorts"][key] = {"id": int(mid), "t": time.time(), "file_id": file_id}
        self.add(mid, None, "short")

    def short(self, key: str) -> dict | None:
        return self.data["shorts"].get(key)

    def drop_short(self, key: str) -> int | None:
        with self.lock:
            v = self.data["shorts"].pop(key, None)
            if v:
                self.data["msgs"].pop(str(v["id"]), None)
            self.save()
            return int(v["id"]) if v else None

    def stale_shorts(self, now: float | None = None) -> list[tuple[str, dict]]:
        now = now or time.time()
        return [(k, v) for k, v in self.data["shorts"].items() if now - float(v.get("t") or 0) >= REFRESH_AFTER]

    def set_card(self, mid) -> None:
        with self.lock:
            self.data["card"] = {"id": int(mid), "t": time.time()}
        self.add(mid, None, "card")

    def card(self) -> dict | None:
        return self.data.get("card")

    def all_ids(self) -> set[int]:
        with self.lock:
            return {int(m) for m in self.data["msgs"]}

    def max_id(self) -> int:
        return int(self.data.get("max_id") or 0)

    def note_id(self, mid) -> None:
        """Номер чужого (вашого) повідомлення — щоб «Очистити чат» охопив усе до нього."""
        if mid:
            with self.lock:
                self.data["max_id"] = max(self.max_id(), int(mid))
                self.save()
