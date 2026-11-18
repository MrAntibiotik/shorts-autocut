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

    def send(self, text, markup=None, ttl="auto", silent=False):
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
                                          "min_damage": 4000, "idle_min": 3,
                                          "version": "1.26", "version_v": 2})   # тестові реплеї — 1.26 (вручну)
        self.bot = FakeBot(self.settings)
        self.rb = replays_bot.ReplaysBot(self.bot)

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def test_menu_and_filter_buttons(self):
        self.rb.show()
        text, markup = self.bot.sent[-1]
        self.assertIn("рівень 8, 9, 10", text)   # типовий фільтр
        self.assertIn("версія: 1.26 (вибрано)", text)
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
        replay_state.save(self.settings, {**replay_state.load(self.settings), "game_dir": str(self.d / "nogame"),
                                          "version": ""})
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

    def test_default_is_newest_site_version_with_game_warning(self):
        replay_state.save(self.settings, {**replay_state.load(self.settings), "version": ""})   # авто
        self.rb.show()
        text, markup = self.bot.sent[-1]
        newest = max((o["value"] for o in replays_bot.load_filters()["version"]),
                     key=lambda v: tuple(map(int, v.split("."))))
        self.assertIn(f"версія: {newest} (найновіша)", text)            # не версія гри (1.26)
        url = next(b["url"] for b in buttons(markup) if "url" in b)
        self.assertIn(f"version={newest}", url)
        self.assertIn(f"⚠️ Ваша гра — 1.26, а реплеї — {newest}", text)  # попередження про різницю
        game = Path(replay_state.effective(self.settings)["game_dir"])
        (game / "version.xml").write_text(f"<version> v.{newest}.1.1 #1</version>")
        self.rb.show()
        self.assertNotIn("⚠️", self.bot.sent[-1][0])                    # гра оновлена — без попередження

    def test_old_saved_version_is_ignored(self):
        replay_state.save(self.settings, {**replay_state.load(self.settings), "version": "1.30", "version_v": 0})
        self.assertEqual(replay_state.effective(self.settings)["version"], "")
        self.rb.show()
        self.assertIn("(найновіша)", self.bot.sent[-1][0])

    def test_deleted_replay_can_be_downloaded_again(self):
        from shorts import replay_site
        self.rb.start = lambda manual: "ok"
        html = (ROOT / "tests" / "data" / "wot_record_list.html").read_text(encoding="utf-8")
        items = replay_site.parse_list(html)
        replay_state.save(self.settings, {**replay_state.load(self.settings), "min_damage": 0})
        good = make_replay(self.d / "g.bin").read_bytes()
        with mock.patch.object(replay_site, "fetch_list", return_value=items):
            self.rb.on_callback({"message": {"message_id": 5, "text": "🎮"}}, "list:1")
        with mock.patch.object(replay_site, "download", return_value=good):
            self.assertEqual(self.rb.on_callback({"message": {}}, "dl:448004"), "⬇ Скачано")
        f = next((self.d / "replays").glob("wr_448004_*.wotreplay"))
        f.with_suffix(".recorded").write_text("rec.mkv")
        f.unlink()                                                          # ви видалили реплей
        self.assertIn("1.", self.rb.list_text())                            # знову не ✅
        self.assertEqual(buttons(self.rb.list_markup())[0]["text"], "⬇1")
        with mock.patch.object(replay_site, "download", return_value=good) as dl:
            self.assertEqual(self.rb.on_callback({"message": {}}, "dl:448004"), "⬇ Скачано")
        dl.assert_called_once()                                             # скачано знову
        self.assertTrue(f.exists())
        self.assertFalse(f.with_suffix(".recorded").exists())               # старий залишок прибрано

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
        self.assertIn("🎬 Leopard1 · Prokhorovka · 7 412 шкоди", self.bot.sent[-1][0])     # жива картка
        self.assertIn("⏸ Запис бою — чекає", self.bot.sent[-1][0])
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

        def fake_run(settings, jopts, ropts, who="", on_stage=None, **kw):
            calls.append((who, jopts.cta_text, ropts.game_dir))
            return {"recorded": 1, "shorts": ["x"], "published": [], "errors": []}
        with mock.patch.object(replay_job, "run", fake_run):
            self.assertTrue(self.rb.start(manual=True).startswith("🎬 Починаю запис: 1"))
            self.rb.thread.join(5)
        self.assertEqual(calls[0][0], "Telegram-бот")
        self.assertIn("Day 1", calls[0][1])
        self.assertIn("🏁 Готово: записано 1", self.bot.sent[-1][0])
        import os
        lock = replay_state.lock_path(self.d / "replays")
        # запис справді йде з вкладки (інший живий процес) → бот не починає, пропонує «Зупинити»
        lock.write_text(f"вкладка, PID {os.getppid()}", encoding="utf-8")
        self.assertEqual(self.rb.start(manual=True), "⏺ Запис уже йде")
        self.assertIn("F|stop", str(self.bot.sent[-1][1]))
        with self.assertRaises(replay_state.Busy):
            with replay_state.running(self.d / "replays", "бот"):
                pass
        # залишок обірваного запису: процесу вже немає → знімається сам
        lock.write_text("вкладка, PID 999999", encoding="utf-8")
        self.assertFalse(replay_state.busy(self.d / "replays"))
        self.assertFalse(lock.exists())
        # залишок від самого бота (запис у ньому вже не йде) → теж знімається, запис починається
        lock.write_text(f"Telegram-бот, PID {os.getpid()}", encoding="utf-8")
        with mock.patch.object(replay_job, "run", fake_run):
            self.assertTrue(self.rb.start(manual=True).startswith("🎬 Починаю запис"))
            self.rb.thread.join(5)

    def test_stop_during_recording(self):
        import time as T
        from shorts import replay_record
        (self.d / "replays").mkdir(exist_ok=True)
        rp = make_replay(self.d / "replays" / "a.wotreplay")

        def fake_run(settings, jopts, ropts, who="", on_stage=None, cancel=None, **kw):
            for _ in range(200):                       # «запис» — доки не натиснуть «Зупинити»
                if cancel():
                    raise replay_record.Cancelled()
                T.sleep(0.01)
            return {"recorded": 0, "shorts": [], "published": [], "errors": []}
        with mock.patch.object(replay_job, "run", fake_run):
            self.rb.start(manual=True)
            T.sleep(0.05)
            self.assertEqual(self.rb.on_callback({"message": {}}, "stop"), "⏹ Зупиняю… (до кількох секунд)")
            self.rb.thread.join(5)
        self.assertIn("⏹ Запис зупинено", self.bot.sent[-1][0])
        self.assertFalse(rp.with_suffix(".failed").exists())                 # реплей лишився в черзі

    def test_stop_button_clears_leftover(self):
        import os
        (self.d / "replays").mkdir(exist_ok=True)
        lock = replay_state.lock_path(self.d / "replays")
        lock.write_text(f"вкладка, PID {os.getppid()}", encoding="utf-8")
        from shorts import replay_record
        with mock.patch.object(replay_record, "kill_game") as kg:
            self.assertEqual(self.rb.on_callback({"message": {}}, "stop"), "⏹ Зупинено")
        kg.assert_called_once()
        self.assertFalse(lock.exists())
        self.assertIn("Позначку «іде запис» знято", self.bot.sent[-1][0])

    def test_instant_record_after_download(self):
        good = make_replay(self.d / "g.bin").read_bytes()
        started = []
        self.rb.start = lambda manual: started.append(manual) or "ok"
        self._doc(good)
        self.assertEqual(started, [False])                            # одразу, без очікування
        self.assertIn("✅ Перевірено — версія 1.26.0.1", self.bot.sent[-1][0])
        # запис іде з вкладки на ПК (інший живий процес) — реплей чекає, картка пояснює, є «Зупинити»
        import os
        replay_state.lock_path(self.d / "replays").write_text(f"вкладка, PID {os.getppid()}", encoding="utf-8")
        self._doc(make_replay(self.d / "h.bin", first=dict(FIRST, playerName="Other")).read_bytes(), "two.wotreplay")
        self.assertEqual(started, [False])
        self.assertIn("чекає: запис іде з вкладки", self.bot.sent[-1][0])
        self.assertIn("F|stop", str(self.bot.sent[-1][1]))

    def test_queue_starts_itself_when_free(self):
        import os
        (self.d / "replays").mkdir(exist_ok=True)
        make_replay(self.d / "replays" / "a.wotreplay")
        started = []
        self.rb.start = lambda manual: started.append(manual) or "ok"
        lock = replay_state.lock_path(self.d / "replays")
        lock.write_text(f"вкладка, PID {os.getppid()}", encoding="utf-8")
        self.rb.tick()
        self.assertEqual(started, [])                                   # вкладка ще пише — чекаємо
        lock.unlink()
        self.rb.tick()
        self.assertEqual(started, [False])                              # звільнилось — почали самі
        self.rb.hold_until = __import__("time").time() + 600            # після помилки — пауза
        self.rb.tick()
        self.assertEqual(started, [False])
        self.rb.hold_until = 0
        self.rb.user_stopped = True                                     # натиснули «⏹» — чекаємо «🎬»
        self.rb.tick()
        self.assertEqual(started, [False])

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

    def test_no_game_keeps_replay_in_queue_and_retry(self):
        from shorts import replay_record
        (self.d / "replays").mkdir(exist_ok=True)
        rp = make_replay(self.d / "replays" / "a.wotreplay")
        jopts = replay_job.ReplayJobOptions(replays_dir=self.d / "replays", publish_dir=None)
        ropts = replay_record.RecordOptions(game_dir=self.d / "nogame")
        with mock.patch.object(replay_record, "STEAM_ROOTS", []), \
                mock.patch.object(replay_record, "KNOWN_DIRS", [str(self.d / "nogame")]), \
                mock.patch.object(replay_record, "ensure_obs") as eo:
            with self.assertRaises(replay_record.RecordError):
                replay_job.run(self.settings, jopts, ropts)
        eo.assert_not_called()                                         # OBS навіть не чіпали
        self.assertFalse(rp.with_suffix(".failed").exists())          # реплей не «зіпсовано»
        self.assertEqual(replay_job.pending_replays(self.d / "replays"), [rp])
        # реплей, що раніше не вдався: «🎬 Записати зібрані» пробує знову
        rp.with_suffix(".failed").write_text("x")
        self.assertEqual(replay_job.pending_replays(self.d / "replays"), [])
        started = []
        with mock.patch.object(replays_bot.threading, "Thread") as th:
            th.return_value.is_alive.return_value = False
            self.assertTrue(self.rb.start(manual=True).startswith("🎬 Починаю запис: 1"))
            th.return_value.start.assert_called_once()
        self.assertFalse(rp.with_suffix(".failed").exists())
        # і повторне скачування такого реплею теж ставить його в чергу
        rp.with_suffix(".failed").write_text("x")
        self.rb.thread = None
        self.rb.start = lambda manual: started.append(manual) or "ok"
        self.rb.accept("a.wotreplay", rp.read_bytes())
        self.assertIn("раніше не записався — ставлю в чергу ще раз", self.bot.sent[-1][0])
        self.assertFalse(rp.with_suffix(".failed").exists())
        self.assertEqual(started, [False])

    def test_live_card_stages(self):
        from replay_cards import Cards
        from shorts.replay_meta import parse
        rp = make_replay(self.d / "card.wotreplay")
        cards = Cards(self.bot)
        cards.create("card", parse(rp), version="2.4.1.1")
        mid = 99
        text = self.bot.sent[-1][0]
        self.assertEqual(self.bot.sent[-1][1], {"inline_keyboard": []})   # інакше Telegram не дасть редагувати
        self.assertIn("✅ Скачано", text)
        self.assertIn("⏸ OBS — чекає", text)
        cards.stage(None, "obs", "run", "перевіряю гру й OBS…")
        cards.stage(None, "obs", "done", "OBS готовий")
        n_sent = len(self.bot.sent)
        cards.stage(rp, "rec", "run", "0:00 / ~8:12")
        self.assertIn("🎥 Запис почався", self.bot.sent[-1][0])        # окреме сповіщення
        cards.stage(rp, "rec", "run", "0:01 / ~8:12")                  # прогрес — без нових повідомлень
        self.assertEqual(len(self.bot.sent), n_sent + 1)
        cards.stage(rp, "rec", "done", "rec.mkv")
        cards.stage(rp, "cut", "done", "шортсів: 1")
        n_before = len(self.bot.sent)
        cards.stage(rp, "post", "done", "replay_card.mp4")
        self.assertEqual(len(self.bot.sent), n_before)              # «готово» — відео (on_eu_short), без дубля текстом
        last = [c for c in self.bot.api.calls if c[0] == "editMessageText"][-1][1]
        self.assertEqual(last["message_id"], mid)
        for line in ("✅ OBS — OBS готовий", "✅ Запис бою — rec.mkv", "✅ Нарізка — шортсів: 1",
                     "✅ Передача в Poster", "⏳ Публікація — Poster EN-каналу поставить"):
            self.assertIn(line, last["text"])
        # помилка на етапі — ❌ з причиною і окреме сповіщення
        rp2 = make_replay(self.d / "card2.wotreplay")
        cards.create("card2", parse(rp2))
        cards.stage(rp2, "cut", "skip", "у бою не знайдено моментів — шортса немає")
        last = [c for c in self.bot.api.calls if c[0] == "editMessageText"][-1][1]["text"]
        self.assertIn("⏭ Нарізка — у бою не знайдено моментів", last)
        self.assertIn("⏭ Публікація — немає шортса", last)
        cards.stage(rp2, "rec", "err", "гра закрилась")
        self.assertTrue(self.bot.sent[-1][0].startswith("❌ Leopard1"))
        self.assertIn("Запис бою: гра закрилась", self.bot.sent[-1][0])

    def test_error_offers_restart(self):
        from replay_cards import Cards
        from shorts.replay_meta import parse
        rp = make_replay(self.d / "err.wotreplay")
        cards = Cards(self.bot)
        cards.create("err", parse(rp))
        cards.stage(rp, "rec", "err", "Гру закрито посеред бою (на 1:46 з ~12:25) — запис перервано")
        text, markup = self.bot.sent[-1]
        self.assertIn("Гру закрито посеред бою", text)
        self.assertEqual([b["callback_data"] for b in buttons(markup)], ["F|retry:err", "F|skip:err"])
        started = []
        self.rb.start = lambda manual: started.append(manual) or "🎬 Починаю запис: 1"
        (self.d / "replays").mkdir(exist_ok=True)
        failed = self.d / "replays" / "err.failed"
        failed.write_text("x")
        self.assertTrue(self.rb.on_callback({"message": {"message_id": 7}}, "retry:err").startswith("🎬"))
        self.assertEqual(started, [True])
        self.assertFalse(failed.exists())                                  # реплей — знову в черзі

    def test_watchdog_stalled_recording(self):
        import threading
        from replay_cards import Cards
        from shorts.replay_meta import parse
        rp = make_replay(self.d / "st.wotreplay")
        self.rb.cards = Cards(self.bot)
        self.rb.cards.create("st", parse(rp))
        self.rb.cards.stage(rp, "rec", "run", "1:46 / ~12:25")
        stop = threading.Event()
        self.rb.thread = threading.Thread(target=stop.wait, daemon=True)  # «завислий» запис
        self.rb.thread.start()
        try:
            self.rb.last_beat = time.time() - 600                         # 10 хв без ознак життя
            self.rb.watchdog()
            text, markup = self.bot.sent[-1]
            self.assertIn("завис", text)
            self.assertIn("F|retry:", [b["callback_data"] for b in buttons(markup)])
            n = len(self.bot.sent)
            self.rb.watchdog()                                             # не повторює
            self.assertEqual(len(self.bot.sent), n)
        finally:
            stop.set()

    def test_watchdog_dead_worker(self):
        from replay_cards import Cards
        from shorts.replay_meta import parse
        rp = make_replay(self.d / "dead.wotreplay")
        self.rb.cards = Cards(self.bot)
        self.rb.cards.create("dead", parse(rp))
        self.rb.cards.stage(rp, "rec", "run", "0:30 / ~8:00")
        self.rb.thread = None                                              # робота впала без звіту
        self.rb.watchdog()
        self.assertIn("перервався", self.bot.sent[-1][0])
        n = len(self.bot.sent)
        self.rb.watchdog()
        self.assertEqual(len(self.bot.sent), n)

    def _problems_setup(self):
        import json
        rd = self.d / "replays"
        rd.mkdir(exist_ok=True)
        out = self.d / "out_en"
        self.settings["paths"]["replays_output_dir"] = str(out)
        a = make_replay(rd / "a.wotreplay")                       # не записався
        a.with_suffix(".failed").write_text("OBS не відповідає", encoding="utf-8")
        b = make_replay(rd / "b.wotreplay")                       # записався, шортса немає
        vb = self.d / "b.mkv"
        vb.write_bytes(b"v")
        b.with_suffix(".recorded").write_text(str(vb), encoding="utf-8")
        c = make_replay(rd / "c.wotreplay")                       # записався, файл запису зник
        c.with_suffix(".recorded").write_text(str(self.d / "gone.mkv"), encoding="utf-8")
        d = make_replay(rd / "d.wotreplay")                       # усе гаразд: є шортс
        vd = self.d / "d.mkv"
        vd.write_bytes(b"v")
        d.with_suffix(".recorded").write_text(str(vd), encoding="utf-8")
        (out / "job_1").mkdir(parents=True)
        (out / "job_1" / "job.json").write_text(json.dumps({"recordings": [
            {"path": str(vd), "shorts": [{"mp4": "x.mp4"}]}, {"path": str(vb), "shorts": []}]}), encoding="utf-8")
        return rd

    def test_problem_replays_kinds(self):
        rd = self._problems_setup()
        items = {it["rp"].stem: it for it in replay_job.problem_replays(self.settings, rd)}
        self.assertEqual({k: v["kind"] for k, v in items.items()}, {"a": "failed", "b": "uncut", "c": "lost"})
        self.assertIn("OBS", items["a"]["reason"])
        self.assertIn("не знайшла моментів", items["b"]["reason"])
        cut_only = replay_job.prepare_retry(list(items.values()))
        self.assertEqual([p.stem for p in cut_only], ["b"])                  # лише нарізка — без гри
        self.assertFalse((rd / "a.failed").exists())                         # знову в черзі на запис
        self.assertFalse((rd / "c.recorded").exists())
        self.assertEqual(sorted(p.stem for p in replay_job.pending_replays(rd)), ["a", "c"])

    def test_deleted_recording_with_short_is_not_a_problem(self):
        rd = self._problems_setup()
        (self.d / "d.mkv").unlink()                                        # прибирання видалило запис, шортс є
        kinds = {it["rp"].stem: it["kind"] for it in replay_job.problem_replays(self.settings, rd)}
        self.assertNotIn("d", kinds)                                         # не «запис зник» — повтору не буде
        self.assertEqual(kinds["c"], "lost")

    def test_bot_shows_problems_and_retries(self):
        rd = self._problems_setup()
        self.rb.show()
        datas = [b.get("callback_data") for b in buttons(self.bot.sent[-1][1])]
        self.assertIn("F|probs", datas)                                      # «♻ Не вдалися (3)»
        self.rb.on_callback({"message": {}}, "probs")
        text, markup = self.bot.sent[-1]
        self.assertIn("Реплеї без шортса: 3", text)
        self.assertIn("F|again:*", [b["callback_data"] for b in buttons(markup)])
        calls = []

        def fake_run(settings, jopts, ropts, who="", on_stage=None, **kw):
            calls.append((jopts.record, [Path(x).stem for x in jopts.only] if jopts.only else "черга"))
            return {"recorded": 0, "shorts": ["s"], "published": [], "errors": []}
        with mock.patch.object(replay_job, "run", fake_run):
            toast = self.rb.on_callback({"message": {"message_id": 3}}, "again:*")
            self.rb.thread.join(5)
        self.assertIn("1 — лише нарізка", toast)
        self.assertEqual(calls[0], (False, ["b"]))                            # спершу нарізка з готового запису
        self.assertEqual(calls[1][1], "черга")                               # потім запис не записаних

    def test_ready_eu_short_sent_as_video_and_can_be_dropped(self):
        from shorts.replay_meta import parse
        info = parse(make_replay(self.d / "v.wotreplay"))
        ready = self.d / "poster_en" / "WoT"
        ready.mkdir(parents=True)
        mp4 = ready / "replay_v_battle_01.mp4"
        mp4.write_bytes(b"v" * 100)
        mp4.with_suffix(".json").write_text("{}", encoding="utf-8")
        files = []
        self.bot.api.send_file = lambda method, field, path, **kw: files.append((method, kw)) or {"message_id": 50}
        with mock.patch("autocut.bot.telegram_bot.make_preview", side_effect=lambda st, p, horizontal=False: p):
            self.rb.on_eu_short(mp4, info)
        method, kw = files[-1]
        self.assertEqual(method, "sendVideo")                        # саме відео, а не лише текст
        self.assertIn("🇬🇧 EU: готовий шортс", kw["caption"])
        self.assertIn("Leopard1", kw["caption"])
        self.assertIn("У черзі EU-каналу Poster", kw["caption"])
        key = buttons(kw["reply_markup"])[0]["callback_data"].split(":", 1)[1]
        self.assertIn("Прибрано", self.rb.on_callback({"message": {"message_id": 50}}, f"eudel:{key}"))
        self.assertFalse(mp4.exists() or mp4.with_suffix(".json").exists())   # з черги Poster EU — прибрано

    def _fake_obs(self, record_dir, recording=True):
        import types
        calls = []

        class FakeObs:
            def __init__(self, host, port, password=""):
                self.cl = types.SimpleNamespace(
                    get_record_directory=lambda: types.SimpleNamespace(record_directory=record_dir),
                    set_record_directory=lambda d: calls.append(("dir", d)),
                    get_profile_list=lambda: types.SimpleNamespace(current_profile_name="Replays"),
                    get_scene_collection_list=lambda: types.SimpleNamespace(current_scene_collection_name="Replays"),
                    set_current_profile=lambda p: calls.append(("profile", p)),
                    set_current_scene_collection=lambda c: calls.append(("collection", c)))
                self.prev, self.prev_dir = ("", ""), ""

            def recording(self):
                return recording

            def stop(self):
                calls.append(("stop",))
                return str(Path(record_dir) / "part.mkv")

        from shorts import replay_record as rr
        FakeObs.restore = rr.Obs.restore                                  # справжнє «повернути як було»
        return FakeObs, calls

    def test_recover_obs_stops_only_replay_recording(self):
        import json
        from shorts import replay_record as rr
        prev = self.d / "obs_prev.json"
        prev.write_text(json.dumps({"profile": "Стрім", "collection": "Мої сцени", "dir": "C:/Videos",
                                    "replay_dir": str(self.d / "eu_rec")}), encoding="utf-8")
        fake, calls = self._fake_obs(str(self.d / "eu_rec"))
        with mock.patch.object(rr, "_prev_file", return_value=prev), mock.patch.object(rr, "Obs", fake):
            res = rr.recover_obs("localhost", 4455)
        self.assertTrue(res["stopped"] and res["restored"])
        self.assertIn(("stop",), calls)                                   # писав реплей — зупинено
        self.assertIn(("profile", "Стрім"), calls)                        # твій профіль — повернуто
        self.assertIn(("dir", "C:/Videos"), calls)
        self.assertFalse(prev.exists())
        prev.write_text(json.dumps({"profile": "Стрім", "replay_dir": str(self.d / "eu_rec")}), encoding="utf-8")
        fake2, calls2 = self._fake_obs("C:/Videos")                       # OBS пише інше (стрім) — не чіпаємо
        with mock.patch.object(rr, "_prev_file", return_value=prev), mock.patch.object(rr, "Obs", fake2):
            res = rr.recover_obs("localhost", 4455)
        self.assertNotIn(("stop",), calls2)
        self.assertFalse(res["stopped"])
        prev.unlink(missing_ok=True)
        fake3, calls3 = self._fake_obs(str(self.d / "eu_rec"))            # реплей не записувався — нічого
        with mock.patch.object(rr, "_prev_file", return_value=prev), mock.patch.object(rr, "Obs", fake3):
            self.assertEqual(rr.recover_obs("localhost", 4455), {"stopped": "", "restored": False})
        self.assertFalse(calls3)

    def test_recover_after_restart_mid_recording(self):
        from shorts import replay_record as rr
        rdir = self.d / "replays"
        rdir.mkdir(exist_ok=True)
        replay_state.lock_path(rdir).write_text("Telegram-бот, PID 999999", encoding="utf-8")   # процесу вже немає
        part = self.d / "part.mkv"
        part.write_bytes(b"x")
        with mock.patch.object(rr, "recover_obs", return_value={"stopped": str(part), "restored": True}), \
                mock.patch.object(rr, "kill_game") as kill:
            self.rb.recover()
        kill.assert_called_once()
        self.assertFalse(replay_state.lock_path(rdir).exists())
        self.assertFalse(part.exists())                                   # недописаний бій прибрано
        self.assertIn("Запис реплею обірвався", self.bot.sent[-1][0])
        n = len(self.bot.sent)
        with mock.patch.object(rr, "recover_obs", return_value={"stopped": "", "restored": False}), \
                mock.patch.object(rr, "kill_game") as kill2:
            self.rb.recover()                                             # звичайний запуск — тихо
        kill2.assert_not_called()
        self.assertEqual(len(self.bot.sent), n)

    def test_old_recordings_deleted_after_3_days(self):
        import os
        rec = self.d / "eu_rec"
        rec.mkdir()
        old, fresh = rec / "old.mkv", rec / "fresh.mkv"
        other, rp = rec / "notes.txt", rec / "x.wotreplay"
        for f in (old, fresh, other, rp):
            f.write_bytes(b"x" * 10)
        week_ago = time.time() - 4 * 86400
        for f in (old, other, rp):
            os.utime(f, (week_ago, week_ago))
        n, size = replay_job.cleanup_recordings(rec, 3)
        self.assertEqual((n, size), (1, 10))
        self.assertFalse(old.exists())                                    # старше 3 днів — видалено
        self.assertTrue(fresh.exists() and other.exists() and rp.exists())  # свіже й не-відео — на місці
        self.assertEqual(replay_job.cleanup_recordings(rec, 0), (0, 0))   # 0 — не видаляти

    def test_records_cleanup_every_3_days(self):
        from unittest import mock
        calls = []
        with mock.patch.object(replay_job, "cleanup_recordings", lambda d, days, now=None: calls.append(days)), \
                mock.patch.object(self.rb, "recording_now", return_value=False):
            t0 = 1_000_000_000.0
            self.assertTrue(self.rb.cleanup_records(self.rb.state(), now=t0))           # перший раз — одразу
            self.assertFalse(self.rb.cleanup_records(self.rb.state(), now=t0 + 3600))   # за годину — ні
            self.assertFalse(self.rb.cleanup_records(self.rb.state(), now=t0 + 2.9 * 86400))
            self.assertTrue(self.rb.cleanup_records(self.rb.state(), now=t0 + 3 * 86400))  # через 3 дні — так
        self.assertEqual(calls, [3.0, 3.0])
        with mock.patch.object(self.rb, "recording_now", return_value=True):            # під час запису — чекає
            self.assertFalse(self.rb.cleanup_records(self.rb.state(), now=t0 + 7 * 86400))

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
