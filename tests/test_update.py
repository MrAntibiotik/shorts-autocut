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


class BackupRollbackTest(unittest.TestCase):
    """Перед оновленням — копія поточної версії; відкат повертає код, не чіпаючи налаштувань і відео."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "autocut").mkdir()
        (self.root / "autocut" / "version.py").write_text('VERSION = "2.0.0"\n', encoding="utf-8")
        (self.root / "autocut" / "a.py").write_text("old = 1\n", encoding="utf-8")
        (self.root / "config").mkdir()
        (self.root / "config" / "settings.json").write_text('{"mine": 1}', encoding="utf-8")
        (self.root / "output").mkdir()
        (self.root / "output" / "short.mp4").write_bytes(b"video")
        (self.root / "requirements.txt").write_text("x\n", encoding="utf-8")
        self.patches = [mock.patch.object(update, "ROOT", self.root),
                        mock.patch.object(update, "BACKUP_DIR", self.root / "backups"),
                        mock.patch.object(update, "VERSION_FILE", self.root / "VERSION.json"),
                        mock.patch.object(update, "_after_code_change", return_value=False)]
        for p in self.patches:
            p.start()
        (self.root / "VERSION.json").write_text('{"sha": "' + "a" * 40 + '", "message": "стара"}', encoding="utf-8")

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_backup_and_rollback(self):
        b = update.backup_current()
        self.assertTrue((b / "autocut" / "a.py").exists())
        self.assertFalse((b / "config").exists())            # налаштування не копіюються
        self.assertFalse((b / "output").exists())            # відео теж
        # «нова версія»
        (self.root / "autocut" / "version.py").write_text('VERSION = "2.1.0"\n', encoding="utf-8")
        (self.root / "autocut" / "a.py").write_text("new = 2\n", encoding="utf-8")
        (self.root / "config" / "settings.json").write_text('{"mine": 2}', encoding="utf-8")
        res = update.restore_backup(update.list_backups()[-1]["name"])
        self.assertEqual(res["version"], "2.0.0")
        self.assertEqual((self.root / "autocut" / "a.py").read_text(encoding="utf-8"), "old = 1\n")
        self.assertEqual(update.code_version(self.root), "2.0.0")
        self.assertEqual((self.root / "config" / "settings.json").read_text(encoding="utf-8"), '{"mine": 2}')
        self.assertTrue((self.root / "output" / "short.mp4").exists())
        # відкат теж зберіг версію 2.1.0 — його можна скасувати
        self.assertIn("2.1.0", [x["version"] for x in update.list_backups()])

    def test_keeps_last_five(self):
        import time
        for i in range(7):
            with mock.patch.object(time, "strftime", side_effect=lambda f, i=i: f"2026111{i}-000000" if "%H" in f else "x"):
                update.backup_current()
        self.assertEqual(len(update.list_backups()), update.KEEP_BACKUPS)

    def test_update_makes_backup_first(self):
        import io as _io
        import zipfile as _zf
        buf = _io.BytesIO()
        with _zf.ZipFile(buf, "w") as z:
            z.writestr("repo-main/shorts/__init__.py", "")
            z.writestr("repo-main/autocut/version.py", 'VERSION = "2.1.0"\n')
            z.writestr("repo-main/autocut/a.py", "new = 2\n")
            z.writestr("repo-main/config/profiles/wot.json", "{}")
            z.writestr("repo-main/tools/merge_profiles.py", "")
        with mock.patch.object(update, "remote_version", return_value={"sha": "b" * 40, "date": "", "message": "нова"}), \
                mock.patch.object(update, "http", return_value=buf.getvalue()), \
                mock.patch.object(update.subprocess, "run"):
            res = update.apply_update(log=lambda m: None)
        self.assertEqual(res["version"], "2.1.0")
        self.assertEqual([b["version"] for b in update.list_backups()], ["2.0.0"])
        self.assertEqual((self.root / "autocut" / "a.py").read_text(encoding="utf-8"), "new = 2\n")
