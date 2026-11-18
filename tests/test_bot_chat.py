"""Чистий чат бота: у чаті лише картка статусу й непідтверджені шортси; решта прибирається сама."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from autocut.bot import chat as chatmod  # noqa: E402
from autocut.bot import telegram_bot as tb  # noqa: E402
from autocut.core.config import load_settings  # noqa: E402


class FakeApi:
    def __init__(self):
        self.n = 100
        self.calls = []
        self.fail_edit_markup = False

    def _msg(self, **extra):
        self.n += 1
        return {"message_id": self.n, **extra}

    def call(self, method, _timeout=40, **p):
        self.calls.append((method, p))
        if method in ("sendMessage", "sendVideo"):
            return self._msg(video={"file_id": p.get("video", "")} if method == "sendVideo" else None)
        if method == "editMessageReplyMarkup" and self.fail_edit_markup:
            raise RuntimeError("Telegram editMessageReplyMarkup: 400 message to edit not found")
        return True

    def send_file(self, method, field, path, _timeout=300, **p):
        self.calls.append((method, {**p, field: str(path)}))
        return self._msg(video={"file_id": f"FILE{self.n + 1}"})

    def count(self, method):
        return sum(1 for m, _ in self.calls if m == method)

    def deleted(self) -> set[int]:
        out = set()
        for m, p in self.calls:
            if m == "deleteMessage":
                out.add(int(p["message_id"]))
            if m == "deleteMessages":
                out |= {int(x) for x in p["message_ids"]}
        return out


class BotChatTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        st = copy.deepcopy(load_settings())
        st["paths"]["output_dir"] = str(d / "output")
        st["paths"]["work_dir"] = str(d / "work")
        self.settings = st
        job = d / "output" / "job_20261115_120000"
        shorts = []
        for i in range(1, 4):
            b = job / f"b{i}"
            b.mkdir(parents=True)
            (b / f"s{i}.mp4").write_bytes(b"v" * 100)
            shorts.append({"mp4": str(b / f"s{i}.mp4"), "name": f"Шортс 0{i}", "duration": 30, "dir": f"b{i}",
                           "battle": i})
        (job / "job.json").write_text(json.dumps({"recordings": [{"path": "x.mkv", "shorts": shorts}]}),
                                      encoding="utf-8")
        self.cfg = {"token": "t", "owner_id": 42, "publish_dir": str(d / "publish")}
        self.patches = [mock.patch.object(tb, "make_preview", side_effect=lambda st, p, horizontal=False: p),
                        mock.patch.object(tb, "save_cfg")]
        for p in self.patches:
            p.start()
        self.api = FakeApi()
        self.bot = tb.Bot(self.settings, self.cfg, api=self.api)
        self.bot.replays = None

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def press(self, data, mid):
        self.bot._on_callback({"id": "q", "data": data, "message": {"message_id": mid, "video": {}, "caption": ""}})

    def test_shorts_stay_until_decided_and_card_counts(self):
        self.bot.cmd_shorts()
        self.assertEqual(len(self.bot.chat.data["shorts"]), 3)
        self.assertEqual(self.api.count("sendVideo"), 3)
        card = self.bot.chat.card()
        self.assertIsNotNone(card)                                       # картка — під шортсами
        self.assertGreater(card["id"], max(v["id"] for v in self.bot.chat.data["shorts"].values()))
        self.bot.cmd_shorts()                                            # повторно — без дублів
        self.assertEqual(self.api.count("sendVideo"), 3)
        key, v = next(iter(self.bot.chat.data["shorts"].items()))
        self.press(f"k|{key}", v["id"])                                  # «✅ Підходить»
        self.assertIn(v["id"], self.api.deleted())                       # зник з чату
        self.assertNotIn(key, self.bot.chat.data["shorts"])
        self.assertTrue(any(Path(self.cfg["publish_dir"]).glob("*.mp4")))
        text = self.bot._card_last[0]
        self.assertIn("На перевірці: 2", text)
        self.assertIn("Схвалено (до публікації): 1", text)

    def test_temp_messages_disappear(self):
        res = self.bot.send("підказка")
        mid = res["message_id"]
        self.bot.janitor()
        self.assertNotIn(mid, self.api.deleted())
        with mock.patch.object(chatmod.time, "time", return_value=time.time() + chatmod.TTL_INFO + 1):
            self.bot.janitor()
        self.assertIn(mid, self.api.deleted())

    def test_shorts_resent_before_48h_limit(self):
        self.bot.cmd_shorts()
        key, v = next(iter(self.bot.chat.data["shorts"].items()))
        later = time.time() + chatmod.REFRESH_AFTER + 1
        with mock.patch.object(chatmod.time, "time", return_value=later):
            self.bot.janitor()
        self.assertIn(v["id"], self.api.deleted())                      # старе — видалено, поки можна
        self.assertNotEqual(self.bot.chat.short(key)["id"], v["id"])     # нове — на місці
        resent = [p for m, p in self.api.calls if m == "sendVideo" and str(p.get("video", "")).startswith("FILE")]
        self.assertTrue(resent)                                          # за file_id, без завантаження

    def test_clear_keeps_card_and_pending_shorts(self):
        self.bot.cmd_shorts()
        self.bot.update_card(force=True)
        self.bot.send("мотлох")
        keep = self.bot.chat.keep_ids()
        self.bot.do_clear()
        gone = self.api.deleted()
        self.assertFalse(keep & gone)
        self.assertTrue(set(range(101, self.api.n - 1)) - keep <= gone | {self.api.n})

    def test_update_waits_for_replay_recording(self):
        import types
        busy = {"v": True}
        self.bot.replays = types.SimpleNamespace(recording_now=lambda: busy["v"])
        with mock.patch("autocut.cli.update.apply_update") as upd:
            self.bot.do_update()
            upd.assert_not_called()                                       # запис іде — не перезапускаємось
            self.assertEqual(self.bot.update_after_rec, "update")
            self.assertIn("оновлю програму одразу після нього", self.api.calls[-1][1]["text"])

    def test_reconcile_restores_buttons_or_resends(self):
        self.bot.cmd_shorts()
        self.bot.reconcile()
        self.assertEqual(self.api.count("editMessageReplyMarkup"), 3)    # кнопки — на місце
        self.api.fail_edit_markup = True
        before = self.api.count("sendVideo")
        self.bot.reconcile()
        self.assertEqual(self.api.count("sendVideo"), before + 3)        # зниклі з чату — знову
        self.assertEqual(len(self.bot.chat.data["shorts"]), 3)


if __name__ == "__main__":
    unittest.main()


class BotAccessTest(BotChatTest):
    """Кілька людей: запит доступу, ролі, рішення синхронно в усіх чатах."""

    def chat_calls(self, chat, method):
        return [p for m, p in self.api.calls if m == method and p.get("chat_id") == chat]

    def start_from(self, uid, name="Олег"):
        self.bot.handle({"update_id": 1, "message": {"message_id": 5, "text": "/start",
                                                     "from": {"id": uid, "first_name": name}, "chat": {"id": uid}}})

    def press_as(self, uid, data, chat=None, mid=1):
        self.bot.handle({"update_id": 2, "callback_query": {"id": "q", "from": {"id": uid}, "data": data,
                         "message": {"message_id": mid, "chat": {"id": chat or uid}, "video": {}, "caption": ""}}})

    def test_request_and_admin_sees_shorts_with_buttons(self):
        self.start_from(77)
        req = self.chat_calls(42, "sendMessage")[-1]
        self.assertIn("Запит доступу", req["text"])
        self.assertEqual([b["callback_data"] for b in req["reply_markup"]["inline_keyboard"][0]],
                         ["A|77|admin", "A|77|viewer", "A|77|no"])
        self.assertEqual(self.bot.access.role(77), "pending")
        self.press_as(77, "A|77|admin")                                   # чужий не може сам собі дати
        self.assertEqual(self.bot.access.role(77), "pending")
        self.press_as(42, "A|77|admin")                                   # власник — «🛠 Адмін»
        self.assertEqual(self.bot.access.role(77), "admin")
        vids = self.chat_calls(77, "sendVideo")
        self.assertEqual(len(vids), 3)                                    # шортси на перевірці — у новий чат
        self.assertTrue(vids[0]["reply_markup"]["inline_keyboard"])        # з кнопками

    def test_viewer_has_no_buttons_and_cannot_decide(self):
        self.bot.access.set(55, "viewer", "Глядач")
        with self.bot.as_chat(55):
            self.bot.cmd_shorts()
        vids = self.chat_calls(55, "sendVideo")
        self.assertEqual(len(vids), 3)
        self.assertEqual(vids[0]["reply_markup"], {"inline_keyboard": []})
        key = next(iter(self.bot._chats[55].data["shorts"]))
        self.press_as(55, f"k|{key}")
        self.assertFalse(any(Path(self.cfg["publish_dir"]).glob("*.mp4")))  # нічого не змінилось
        self.bot.handle({"update_id": 3, "message": {"message_id": 9, "text": "/update", "from": {"id": 55},
                                                     "chat": {"id": 55}}})
        self.assertIn("лише на перегляд", self.chat_calls(55, "sendMessage")[-1]["text"])

    def test_decision_removes_short_from_everyones_chat(self):
        self.bot.access.set(77, "admin", "Адмін")
        self.bot.cmd_shorts()                                             # у власника
        with self.bot.as_chat(77):
            self.bot.cmd_shorts()                                         # і в адміна
        key, mine = next(iter(self.bot._chats[42].data["shorts"].items()))
        theirs = self.bot._chats[77].data["shorts"][key]["id"]
        self.press_as(77, f"k|{key}", mid=theirs)                         # вирішив адмін
        self.assertNotIn(key, self.bot._chats[42].data["shorts"])         # у власника теж зник
        self.assertIn(mine["id"], {p["message_id"] for p in self.chat_calls(42, "deleteMessage")})
        self.assertTrue(any(Path(self.cfg["publish_dir"]).glob("*.mp4")))

    def test_admin_cannot_update_or_manage_access(self):
        self.bot.access.set(77, "admin", "Адмін")
        self.press_as(77, "U|go")
        self.press_as(77, "A|88|admin")
        self.assertIsNone(self.bot.access.role(88))
        self.bot.handle({"update_id": 4, "message": {"message_id": 9, "text": "/users", "from": {"id": 77},
                                                     "chat": {"id": 77}}})
        self.assertIn("лише власник", self.chat_calls(77, "sendMessage")[-1]["text"])

    def test_remove_access(self):
        self.bot.access.set(77, "viewer", "Глядач")
        with self.bot.as_chat(77):
            self.bot.cmd_shorts()
        self.press_as(42, "A|77|no")
        self.assertIsNone(self.bot.access.role(77))
        self.assertFalse(self.bot._chats[77].data["shorts"])              # його шортси прибрано
        self.assertNotIn(77, self.bot.access.recipients())


    def test_owner_menu_has_access_button_and_invite(self):
        self.bot.access.set(77, "admin", "Адмін")
        texts = lambda m: [b["text"] for row in m["keyboard"] for b in row]       # noqa: E731
        with self.bot.as_chat(42):
            self.assertIn("👥 Доступ: адміністратори й перегляд", texts(self.bot.menu()))
        with self.bot.as_chat(77):
            self.assertNotIn("👥 Доступ: адміністратори й перегляд", texts(self.bot.menu()))
        self.start_from(88, "Нова")                                         # запит чекає
        orig = self.api.call
        self.api.call = lambda method, _timeout=40, **p: ({"username": "rizak_bot"} if method == "getMe"
                                                         else orig(method, _timeout, **p))
        self.bot.handle({"update_id": 7, "message": {"message_id": 11, "text": "👥 Доступ: адміністратори й перегляд",
                                                     "from": {"id": 42}, "chat": {"id": 42}}})
        msg = self.chat_calls(42, "sendMessage")[-1]
        self.assertIn("Чекають рішення", msg["text"])
        datas = [b["callback_data"] for row in msg["reply_markup"]["inline_keyboard"] for b in row]
        self.assertIn("A|88|admin", datas)
        self.assertIn("A|invite|admin", datas)
        self.press_as(42, "A|invite|admin")
        self.assertIn("https://t.me/rizak_bot?start=join", self.chat_calls(42, "sendMessage")[-1]["text"])

for _name in [n for n in vars(BotChatTest) if n.startswith("test_")]:
    setattr(BotAccessTest, _name, None)
