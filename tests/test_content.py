"""Увесь відео-контент в одній папці (Робочий стіл\\Контент), перенесення старих папок з переписуванням шляхів."""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import autocut.core.config as config  # noqa: E402
from autocut.core import content  # noqa: E402


class ContentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.app, self.desk = t / "ShortsTool", t / "Desktop"
        self.app.mkdir()
        self.desk.mkdir()
        self.env = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = str(t / "appdata")
        self.patches = [mock.patch.object(config, "ROOT", self.app),
                        mock.patch.object(config, "desktop_dir", return_value=self.desk)]
        for p in self.patches:
            p.start()
        self.settings = copy.deepcopy(config.DEFAULT_SETTINGS)
        self.settings["paths"]["output_dir"] = "output"            # як у старому settings.json

    def tearDown(self):
        for p in self.patches:
            p.stop()
        if self.env is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = self.env
        self.tmp.cleanup()

    def make_legacy(self):
        out = self.app / "output"
        job = out / "job_20261113_233821"
        (job / "b5").mkdir(parents=True)
        mp4 = job / "b5" / "s5.mp4"
        mp4.write_bytes(b"v")
        hl = self.desk / "Хайлайти"
        hl.mkdir()
        (hl / "h5.mp4").write_bytes(b"h")
        pub = out / "_до_публікації"
        pub.mkdir()
        (pub / "s4.mp4").write_bytes(b"p")
        (job / "job.json").write_text(json.dumps({"recordings": [{"path": "C:/Videos/rec.mkv",
            "shorts": [{"mp4": str(mp4)}], "highlights": [{"mp4": str(hl / "h5.mp4")}]}]}), encoding="utf-8")
        (out / "_telegram.json").write_text(json.dumps({"published": {"x|4": str(pub / "s4.mp4")}}), encoding="utf-8")
        eu = self.app / "output_replays_en"
        (eu / "job_1").mkdir(parents=True)
        (eu / "job_1" / "r.mp4").write_bytes(b"r")
        (eu / "_published_en.json").write_text(json.dumps([str(eu / "job_1" / "r.mp4")]), encoding="utf-8")
        recs = self.desk / "wot replays eu"
        recs.mkdir()
        (recs / "rec1.mkv").write_bytes(b"o")
        replays = self.app / "replays"
        replays.mkdir()
        (replays / "1.recorded").write_text(str(recs / "rec1.mkv"), encoding="utf-8")
        return replays

    def test_folders_on_desktop_by_channel(self):
        c = self.desk / "Контент"
        self.assertEqual(config.output_dir(self.settings), c / "Мій канал" / "Нарізки")
        self.assertEqual(config.highlights_dir(self.settings), c / "Мій канал" / "Хайлайти")
        self.assertEqual(content.folder(self.settings, "publish"), c / "Мій канал" / "До публікації")
        from autocut.eu.replay_job import replays_output_dir
        self.assertEqual(replays_output_dir(self.settings), c / "EU-канал" / "Нарізки")

    def test_migrate_moves_and_rewrites_paths(self):
        replays = self.make_legacy()
        cfg = {"publish_dir": str(self.app / "output" / "_до_публікації")}
        moves = content.ensure_migrated(self.settings, cfg, replays)
        self.assertTrue(moves)
        c = self.desk / "Контент"
        cuts, hl, pub = c / "Мій канал" / "Нарізки", c / "Мій канал" / "Хайлайти", c / "Мій канал" / "До публікації"
        eu_cuts, eu_rec = c / "EU-канал" / "Нарізки", c / "EU-канал" / "Записи реплеїв"
        self.assertTrue((cuts / "job_20261113_233821" / "b5" / "s5.mp4").exists())
        self.assertTrue((hl / "h5.mp4").exists())
        self.assertTrue((pub / "s4.mp4").exists())
        self.assertTrue((eu_cuts / "job_1" / "r.mp4").exists())
        self.assertTrue((eu_rec / "rec1.mkv").exists())
        self.assertFalse((self.app / "output").exists())             # старих папок не лишилось
        job = json.loads((cuts / "job_20261113_233821" / "job.json").read_text(encoding="utf-8"))
        self.assertEqual(job["recordings"][0]["shorts"][0]["mp4"], str(cuts / "job_20261113_233821" / "b5" / "s5.mp4"))
        self.assertEqual(job["recordings"][0]["highlights"][0]["mp4"], str(hl / "h5.mp4"))
        self.assertEqual(job["recordings"][0]["path"], "C:/Videos/rec.mkv")            # оригінал стріму не чіпаємо
        marks = json.loads((cuts / "_telegram.json").read_text(encoding="utf-8"))
        self.assertEqual(marks["published"]["x|4"], str(pub / "s4.mp4"))
        self.assertEqual(json.loads((eu_cuts / "_published_en.json").read_text(encoding="utf-8")),
                         [str(eu_cuts / "job_1" / "r.mp4")])
        self.assertEqual((replays / "1.recorded").read_text(encoding="utf-8"), str(eu_rec / "rec1.mkv"))
        self.assertEqual(cfg["publish_dir"], str(pub))                                  # бот публікує в нову
        poster = json.loads(content.discovery_file().read_text(encoding="utf-8"))
        self.assertIn([str(self.desk / "Хайлайти"), str(hl)], poster["moved"])          # Poster знатиме, куди
        self.assertEqual(poster["folders"]["highlights"], str(hl))
        self.assertEqual(content.ensure_migrated(self.settings, cfg, replays), [])      # удруге — нічого

    def test_onedrive_desktop_keeps_video_out_of_cloud(self):
        """Робочий стіл у OneDrive — відео в «Відео»\\Контент (поза хмарою), старі папки звідти теж виносимо."""
        od = Path(self.tmp.name) / "OneDrive" / "Desktop"
        (od / "Хайлайти").mkdir(parents=True)
        (od / "Хайлайти" / "h.mp4").write_bytes(b"h")
        videos = Path(self.tmp.name) / "Videos"
        with mock.patch.object(config, "desktop_dir", return_value=od), \
                mock.patch.object(content, "videos_dir", return_value=videos):
            self.assertEqual(content.content_dir(self.settings), videos / "Контент")
            self.assertTrue(content.in_onedrive(od))
            content.ensure_migrated(self.settings, {}, None)
            self.assertTrue((videos / "Контент" / "Мій канал" / "Хайлайти" / "h.mp4").exists())
            self.assertFalse((od / "Хайлайти").exists())
            self.assertFalse((od / "Контент").exists())            # на робочому столі (у хмарі) — жодного відео

    def test_custom_publish_folder_is_left_alone(self):
        self.make_legacy()
        mine = str(Path(self.tmp.name) / "Poster" / "ready")
        cfg = {"publish_dir": mine}
        content.ensure_migrated(self.settings, cfg, None)
        self.assertEqual(cfg["publish_dir"], mine)

    def test_not_during_processing(self):
        self.make_legacy()
        with mock.patch("autocut.core.resolve_export.read_status",
                        return_value={"status": "running", "heartbeat": __import__("time").time()}):
            self.assertEqual(content.ensure_migrated(self.settings, {}, None), [])
        self.assertTrue((self.app / "output").exists())


if __name__ == "__main__":
    unittest.main()
