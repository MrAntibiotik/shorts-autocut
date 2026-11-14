"""Реплеї в Telegram-боті: меню з фільтром, кнопки, файл .wotreplay, запуск запису (Telegram і запис підмінено)."""
from __future__ import annotations

import io
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import replays_bot  # noqa: E402
from shorts import replay_job, replay_state  # noqa: E402
from tests.test_replays import FIRST, make_replay  # noqa: E402


class FakeApi:
    base = "https://api.telegram.org/botTOKEN/"

    def __init__(self):
        self.calls = []

    def call(self, method, **kw):
        self.calls.append((method, kw))
        if method == "getFile":
            return {"file_path": "documents/file_1.wotreplay"}
        return {"message_id": len(self.calls)}


class FakeBot:
    def __init__(self, settings):
        self.settings = settings
        self.api = FakeApi()
        self.owner = 1
        self.sent = []

    def send(self, text, markup=None):
        self.sent.append((text, markup))
        return {"message_id": 99}


def buttons(markup):
    return [b for row in markup["inline_keyboard"] for b in row]


class ReplaysBotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.settings = {"paths": {"work_dir": str(self.d / "work")}}
        self.p = mock.patch.object(replay_state, "work_dir", lambda s: Path(s["paths"]["work_dir"]))
        self.p.start()
        game = self.d / "game"
        game.mkdir()
        (game / "version.xml").write_text("<version.xml><version> v.1.26.0.1 #1234</version></version.xml>")
        replay_state.save(self.settings, {"game_dir": str(game), "replays_dir": str(self.d / "replays"),
                                          "min_damage": 4000, "idle_min": 3})
        self.bot = FakeBot(self.settings)
        self.rb = replays_bot.ReplaysBot(self.bot)

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def test_menu_and_filter_buttons(self):
        self.rb.show()
        text, markup = self.bot.sent[-1]
        self.assertIn("рівень 8, 9, 10", text)   # типовий фільтр
        self.assertIn("версія: 1.26", text)
        url = next(b["url"] for b in buttons(markup) if "url" in b)
        self.assertEqual(url, "https://wot-record.com/replays/date/desc?tier=8,9,10&version=1.26&mastery=4")
        msg = {"message_id": 5}
        self.rb.on_callback({"message": msg}, "t:10")          # прибрати 10
        self.rb.on_callback({"message": msg}, "y:2")           # СТ
        self.rb.on_callback({"message": msg}, "s")             # сортування → наступне
        self.rb.on_callback({"message": msg}, "d")             # шкода 4000 → 5000
        st = replay_state.effective(self.settings)
        self.assertEqual(st["wr_sel"]["tier"], ["8", "9"])
        self.assertEqual(st["wr_sel"]["type"], ["2"])
        self.assertEqual(st["sort"], "view")
        self.assertEqual(st["min_damage"], 5000)
        edit = [c for c in self.bot.api.calls if c[0] == "editMessageText"][-1][1]
        url = next(b["url"] for b in buttons(edit["reply_markup"]) if "url" in b)
        self.assertEqual(url, "https://wot-record.com/replays/view/desc?tier=8,9&type=2&version=1.26&mastery=4")
        self.rb.on_callback({"message": msg}, "v:nation")
        edit = [c for c in self.bot.api.calls if c[0] == "editMessageText"][-1][1]
        self.assertIn("Польща", [b["text"] for b in buttons(edit["reply_markup"])])
        self.rb.on_callback({"message": msg}, "n:9")
        self.assertEqual(replay_state.effective(self.settings)["wr_sel"]["nation"], ["9"])
        self.assertEqual(self.rb.on_callback({"message": msg}, "r"), "♻ Фільтр скинуто")

    def _doc(self, data: bytes, name="good.wotreplay"):
        with mock.patch.object(replays_bot.urllib.request, "urlopen", return_value=io.BytesIO(data)) as uo:
            self.rb.on_document({"document": {"file_name": name, "file_id": "F1", "file_size": len(data)}})
        return uo

    def test_document_accepted_and_rejected(self):
        good = make_replay(self.d / "g.bin").read_bytes()
        uo = self._doc(good)
        self.assertEqual(uo.call_args[0][0], "https://api.telegram.org/file/botTOKEN/documents/file_1.wotreplay")
        self.assertTrue((self.d / "replays" / "good.wotreplay").exists())
        self.assertIn("Leopard1 · Prokhorovka · 7,412 шкоди", self.bot.sent[-1][0])
        self.assertIn("У черзі на запис: 1", self.bot.sent[-1][0])
        self.assertGreater(self.rb.last_received, 0)
        old = make_replay(self.d / "o.bin", first=dict(FIRST, clientVersionFromXml="1.25.0.0")).read_bytes()
        self._doc(old, "old.wotreplay")
        self.assertIn("не беру — інша версія гри", self.bot.sent[-1][0])
        self.assertFalse((self.d / "replays" / "old.wotreplay").exists())
        self.rb.on_document({"document": {"file_name": "x.txt", "file_id": "F2"}})
        self.assertIn("Це не реплей", self.bot.sent[-1][0])

    def test_start_runs_once_and_respects_lock(self):
        (self.d / "replays").mkdir()
        make_replay(self.d / "replays" / "a.wotreplay")
        calls = []

        def fake_run(settings, jopts, ropts, who="", on_short=None, **kw):
            calls.append((who, jopts.cta_text, ropts.game_dir))
            on_short(Path("x.mp4"), None)
            return {"recorded": 1, "shorts": ["x"], "published": [], "errors": []}
        with mock.patch.object(replay_job, "run", fake_run):
            self.assertTrue(self.rb.start(manual=True).startswith("🎬 Починаю запис: 1"))
            self.rb.thread.join(5)
        self.assertEqual(calls[0][0], "Telegram-бот")
        self.assertIn("Day 1", calls[0][1])
        self.assertIn("🏁 Готово: записано 1", self.bot.sent[-1][0])
        # запис уже йде з вкладки → бот не починає
        with replay_state.running(self.d / "replays", "вкладка"):
            self.assertEqual(self.rb.start(manual=True), "⏺ Запис уже йде")
            with self.assertRaises(replay_state.Busy):
                with replay_state.running(self.d / "replays", "бот"):
                    pass
        self.assertFalse(replay_state.busy(self.d / "replays"))

    def test_auto_start_after_idle(self):
        started = []
        self.rb.start = lambda manual: started.append(manual) or "ok"
        self.rb.last_received = time.time()
        self.rb.tick()
        self.assertEqual(started, [])
        self.rb.last_received = time.time() - 4 * 60
        self.rb.tick()
        self.assertEqual(started, [False])
        self.assertEqual(self.rb.last_received, 0.0)


if __name__ == "__main__":
    unittest.main()
