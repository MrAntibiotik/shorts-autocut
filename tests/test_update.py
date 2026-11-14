"""Оновлення: остання версія і «що нового» зі стрічки комітів GitHub (без ліміту API)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import update  # noqa: E402

A, B, C = "a" * 40, "b" * 40, "c" * 40
FEED = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><id>tag:github.com,2008:Grit::Commit/{A}</id><title>
      Merge pull request #18 from x/y
  </title><updated>2026-11-14T22:11:52Z</updated>
  <content type="html">&lt;pre&gt;Merge pull request #18 from x/y

Записи реплеїв — у папку «wot replays eu»&lt;/pre&gt;</content></entry>
  <entry><id>tag:github.com,2008:Grit::Commit/{B}</id><title>Recordings go to the Desktop</title>
  <updated>2026-11-14T22:10:00Z</updated></entry>
  <entry><id>tag:github.com,2008:Grit::Commit/{C}</id><title>Older change</title>
  <updated>2026-11-14T20:00:00Z</updated></entry>
</feed>""".encode()


class UpdateFeedTest(unittest.TestCase):
    def setUp(self):
        update._FEED.update(t=0.0, items=[])

    def test_remote_version_and_changes_without_api(self):
        with mock.patch.object(update, "http", return_value=FEED) as h:
            rv = update.remote_version()
            self.assertEqual((rv["sha"], rv["message"]), (A, "Записи реплеїв — у папку «wot replays eu»"))
            self.assertEqual(update.changes_since(C), ["Recordings go to the Desktop", "Записи реплеїв — у папку «wot replays eu»"])
            self.assertEqual(update.changes_since(A), [])
        self.assertEqual(h.call_count, 1)                                  # кеш: один запит на все
        self.assertTrue(all("api.github.com" not in c.args[0] for c in h.call_args_list))

    def test_rate_limit_message(self):
        import urllib.error

        def boom(url, timeout=60):
            raise urllib.error.HTTPError(url, 403, "rate limit exceeded", {}, None)
        with mock.patch.object(update, "http", side_effect=boom):
            with self.assertRaises(RuntimeError) as cm:
                update.remote_version()
        self.assertIn("спробуйте за годину", str(cm.exception))


class BotCheckRateTest(unittest.TestCase):
    def test_at_most_hourly_and_survives_restart(self):
        import time as T
        import telegram_bot as tb
        calls = []
        saved = {}
        with mock.patch.object(update, "remote_version", side_effect=lambda: calls.append(1) or {"sha": A, "message": "m"}), \
                mock.patch.object(update, "local_version", return_value={"sha": A}), \
                mock.patch.object(tb, "save_cfg", side_effect=lambda c: saved.update(c)):
            class Api:
                base = "x"

                def call(self, *a, **k):
                    return {"message_id": 1}
            cfg = {"token": "T", "owner_id": 1}
            b = tb.Bot({"paths": {"work_dir": "/tmp"}}, cfg, api=Api())
            b.check_updates()
            b.check_updates()                                            # одразу вдруге — без запиту
            self.assertEqual(len(calls), 1)
            b2 = tb.Bot({"paths": {"work_dir": "/tmp"}}, dict(saved), api=Api())   # «перезапуск» бота
            b2.check_updates()
            self.assertEqual(len(calls), 1)                              # час останньої перевірки збережено
            with mock.patch.object(tb.time, "time", return_value=T.time() + 3601):
                b2.check_updates()
            self.assertEqual(len(calls), 2)                              # через годину — можна


if __name__ == "__main__":
    unittest.main()
