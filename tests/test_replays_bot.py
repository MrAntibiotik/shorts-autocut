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

    def delete_msg(self, mid):
        self.api.calls.append(("deleteMessage", {"message_id": mid}))


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
        st = replay_state.effective(self.settings)
        self.assertEqual(st["wr_sel"]["tier"], ["8", "9"])
        self.assertEqual(st["wr_sel"]["type"], ["2"])
        self.assertEqual(st["sort"], "view")
        edit = [c for c in self.bot.api.calls if c[0] == "editMessageText"][-1][1]
        url = next(b["url"] for b in buttons(edit["reply_markup"]) if "url" in b)
        self.assertEqual(url, "https://wot-record.com/replays/view/desc?tier=8,9&type=2&version=1.26&mastery=4")
        self.rb.on_callback({"message": msg}, "v:nation")
        edit = [c for c in self.bot.api.calls if c[0] == "editMessageText"][-1][1]
        self.assertIn("Польща", [b["text"] for b in buttons(edit["reply_markup"])])
        self.rb.on_callback({"message": msg}, "n:9")
        self.assertEqual(replay_state.effective(self.settings)["wr_sel"]["nation"], ["9"])
        self.assertEqual(self.rb.on_callback({"message": msg}, "r"), "♻ Фільтр скинуто")

    def test_type_own_damage_and_assist(self):
        msg = {"message_id": 5}
        self.rb.on_callback({"message": msg}, "ask:min_damage")
        self.assertEqual(self.rb.awaiting, "min_damage")
        self.assertIn("Напишіть мінімальну шкоду", self.bot.sent[-1][0])
        self.assertTrue(self.rb.on_text("5 500"))
        self.assertIsNone(self.rb.awaiting)
        self.assertEqual(replay_state.effective(self.settings)["min_damage"], 5500)
        self.assertIn("шкода від 5 500", self.bot.sent[-1][0])           # меню показано знову
        self.rb.on_callback({"message": msg}, "ask:min_assist")
        self.assertTrue(self.rb.on_text("дуже багато"))                    # не число — питаємо ще раз
        self.assertEqual(self.rb.awaiting, "min_assist")
        self.rb.on_text("3.5k")
        st = replay_state.effective(self.settings)
        self.assertEqual(st["min_assist"], 3500)
        self.assertIn("шкода від 5 500 або засвіт від 3 500", self.bot.sent[-1][0])
        labels = [b["text"] for b in buttons(self.bot.sent[-1][1])]
        self.assertIn("💥 Шкода від 5 500", labels)
        self.assertIn("🔦 Засвіт від 3 500", labels)
        # швидкий вибір кнопкою
        self.rb.on_callback({"message": {"message_id": 7, "text": "✍️ Напишіть мінімальну шкоду"}},
                            "set:min_damage=0")
        self.assertEqual(replay_state.effective(self.settings)["min_damage"], 0)
        self.assertFalse(self.rb.on_text("123"))                            # питання вже знято

    def test_newest_version_without_game(self):
        replay_state.save(self.settings, {**replay_state.load(self.settings), "game_dir": str(self.d / "nogame")})
        self.rb.show()
        text, markup = self.bot.sent[-1]
        newest = max((o["value"] for o in replays_bot.load_filters()["version"]),
                     key=lambda v: tuple(map(int, v.split("."))))
        self.assertIn(f"версія: {newest} (найновіша)", text)
        url = next(b["url"] for b in buttons(markup) if "url" in b)
        self.assertIn(f"version={newest}", url)
        # реплей новішого патча, що вже потрапив у програму, піднімає «найновішу»
        (self.d / "replays").mkdir(exist_ok=True)
        (self.d / "replays" / "x.json").write_text('{"version": "9.1.0.0"}', encoding="utf-8")
        self.rb.show()
        self.assertIn("версія: 9.1 (найновіша)", self.bot.sent[-1][0])
        # і старі реплеї відсіюються за нею
        old = make_replay(self.d / "o.bin").read_bytes()          # 1.26
        self._doc(old, "old.wotreplay")
        self.assertIn("інша версія гри (1.26.0.1)", self.bot.sent[-1][0])

    def test_site_list_and_download(self):
        from shorts import replay_site
        self.rb.start = lambda manual: "ok"
        html = (ROOT / "tests" / "data" / "wot_record_list.html").read_text(encoding="utf-8")
        items = replay_site.parse_list(html)
        self.assertEqual([i["id"] for i in items], ["448004", "448003", "448002"])
        self.assertEqual((items[0]["tank"], items[0]["map"], items[0]["damage"], items[0]["mastery"]),
                         ("SMV CC-56", "Province", 5164, 4))
        replay_state.save(self.settings, {**replay_state.load(self.settings), "min_damage": 5000, "min_assist": 0})
        good = make_replay(self.d / "g.bin", first=dict(FIRST, clientVersionFromXml="1.26.0.1")).read_bytes()
        with mock.patch.object(replay_site, "fetch_list", return_value=items) as fl:
            self.rb.on_callback({"message": {"message_id": 5, "text": "🎮 Реплеї"}}, "list:1")
        self.assertEqual(fl.call_args[0][1], 1)
        text, markup = self.bot.sent[-1]
        self.assertIn("сторінка 1", text)
        self.assertIn("сховано слабших за пороги: 1", text)          # 2568 шкоди < 5000
        self.assertIn("1. SMV CC-56 · Province · 💥5 164", text)
        self.assertEqual([b.get("callback_data") for b in buttons(markup)][:2], ["F|dl:448004", "F|dl:448002"])
        with mock.patch.object(replay_site, "download", return_value=good) as dl:
            toast = self.rb.on_callback({"message": {"message_id": 6, "text": "📋 Бої"}}, "dl:448004")
        self.assertEqual(toast, "⬇ Скачано")
        self.assertEqual(dl.call_args[0][:2], ("https://wot-record.com", "448004"))
        self.assertTrue(list((self.d / "replays").glob("wr_448004_*.wotreplay")))
        edit = [c for c in self.bot.api.calls if c[0] == "editMessageText"][-1][1]
        self.assertIn("✅ SMV CC-56", edit["text"])
        self.assertEqual(buttons(edit["reply_markup"])[0]["text"], "✅1")
        with mock.patch.object(replay_site, "download") as dl:          # вдруге — без запиту
            self.assertEqual(self.rb.on_callback({"message": {}}, "dl:448004"), "✅ Уже скачано")
        dl.assert_not_called()
        with mock.patch.object(replay_site, "fetch_list", side_effect=replay_site.SiteError("сайт відмовив (429)")):
            self.rb.on_callback({"message": {"message_id": 6, "text": "📋 Бої"}}, "list:2")
        self.assertIn("Список не завантажився: сайт відмовив (429)", self.bot.sent[-1][0])

    def test_parse_number(self):
        P = replays_bot.parse_number
        self.assertEqual([P("5500"), P("5 500"), P("5.5k"), P("7к"), P("5.500"), P("0")],
                         [5500, 5500, 5500, 7000, 5500, 0])
        self.assertEqual([P("abc"), P("99999"), P("5.5")], [None, None, None])

    def _doc(self, data: bytes, name="good.wotreplay"):
        with mock.patch.object(replays_bot.urllib.request, "urlopen", return_value=io.BytesIO(data)) as uo:
            self.rb.on_document({"document": {"file_name": name, "file_id": "F1", "file_size": len(data)}})
        return uo

    def test_document_accepted_and_rejected(self):
        self.rb.start = lambda manual: "ok"          # запис тут не потрібен
        good = make_replay(self.d / "g.bin").read_bytes()
        uo = self._doc(good)
        self.assertEqual(uo.call_args[0][0], "https://api.telegram.org/file/botTOKEN/documents/file_1.wotreplay")
        self.assertTrue((self.d / "replays" / "good.wotreplay").exists())
        self.assertIn("Leopard1 · Prokhorovka · 7,412 шкоди", self.bot.sent[-1][0])
        self.assertIn("Запускаю: OBS → запис бою", self.bot.sent[-1][0])
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

    def test_instant_record_after_download(self):
        good = make_replay(self.d / "g.bin").read_bytes()
        started = []
        self.rb.start = lambda manual: started.append(manual) or "ok"
        self._doc(good)
        self.assertEqual(started, [False])                            # одразу, без очікування
        self.assertIn("Запускаю: OBS → запис бою → нарізка → Poster", self.bot.sent[-1][0])
        # запис уже йде — другий реплей стає в чергу, нового запуску немає
        with replay_state.running(self.d / "replays", "бот"):
            self._doc(make_replay(self.d / "h.bin", first=dict(FIRST, playerName="Other")).read_bytes(), "two.wotreplay")
        self.assertEqual(started, [False])
        self.assertIn("у черзі", self.bot.sent[-1][0])

    def test_work_loops_until_queue_empty(self):
        (self.d / "replays").mkdir()
        a = make_replay(self.d / "replays" / "a.wotreplay")
        calls = []

        def fake_run(settings, jopts, ropts, who="", on_short=None, **kw):
            calls.append(ropts.obs_exe)
            done = sorted((self.d / "replays").glob("*.wotreplay"))
            for p in done:
                if not p.with_suffix(".recorded").exists():
                    p.with_suffix(".recorded").write_text("x")
                    if p == a:          # під час першого запису скачали ще один
                        make_replay(self.d / "replays" / "b.wotreplay")
                    break
            return {"recorded": 1, "shorts": ["s"], "published": ["p"], "errors": []}
        with mock.patch.object(replay_job, "run", fake_run):
            self.rb.start(manual=False)
            self.rb.thread.join(5)
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[0].endswith("obs64.exe"))
        self.assertIn("🏁 Готово: записано 2, шортсів 2, у Poster 2", self.bot.sent[-1][0])

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
