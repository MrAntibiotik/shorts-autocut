"""Реплеї: читання .wotreplay, скачування за фільтром (сайт підмінено), json для Poster.

Запуск: python -m pytest tests (або python -m unittest discover tests)."""
from __future__ import annotations

import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shorts import replay_download as rd  # noqa: E402
from shorts.replay_job import ReplayJobOptions, pending_replays, poster_json, publish  # noqa: E402
from shorts.replay_meta import MAGIC, ReplayError, parse, parse_version, same_version, title_en  # noqa: E402
from shorts.util import first_number  # noqa: E402

FIRST = {"playerName": "SomePlayer", "playerVehicle": "germany-G89_Leopard1", "mapName": "05_prohorovka",
         "mapDisplayName": "Prokhorovka", "clientVersionFromXml": "1.26.0.1 #1234", "serverName": "EU2",
         "regionCode": "EU", "dateTime": "10.11.2026 21:33:05", "battleType": 1,
         "vehicles": {"1": {"name": "SomePlayer", "vehicleType": "germany:G89_Leopard1", "team": 1}}}
RESULTS = [{"common": {"duration": 412, "winnerTeam": 1},
            "personal": {"avatar": {"team": 1}, "12345": {"damageDealt": 7412, "kills": 3, "team": 1,
                                                          "damageAssistedRadio": 1100, "damageAssistedTrack": 150,
                                                          "damageBlockedByArmor": 0, "xp": 1500, "deathReason": -1}}},
           {}, {}]


def make_replay(path: Path, first=FIRST, results=RESULTS) -> Path:
    blocks = [json.dumps(first).encode()] + ([json.dumps(results).encode()] if results is not None else [])
    data = struct.pack("<II", MAGIC, len(blocks))
    for b in blocks:
        data += struct.pack("<I", len(b)) + b
    path.write_bytes(data + b"\x00" * 64)   # далі — стиснуті пакети, які не читаємо
    return path


class ReplayMetaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_parse_complete(self):
        info = parse(make_replay(self.d / "a.wotreplay"))
        self.assertEqual(info.player, "SomePlayer")
        self.assertEqual(info.vehicle, "Leopard1")
        self.assertEqual(info.nation, "germany")
        self.assertEqual(info.map_name, "Prokhorovka")
        self.assertEqual(info.version_tuple, (1, 26, 0, 1))
        self.assertTrue(info.complete)
        self.assertEqual((info.damage, info.kills, info.assist), (7412, 3, 1250))
        self.assertEqual(info.duration_s, 412)
        self.assertTrue(info.won)
        self.assertTrue(info.survived)
        self.assertEqual(title_en(info), "Leopard1 — 7,412 damage on Prokhorovka 🔥")
        self.assertEqual(info.stats_line, "7,412 damage · 3 kills · 1,250 assist")

    def test_localized_map_name(self):
        info = parse(make_replay(self.d / "m.wotreplay", first=dict(FIRST, mapName="11_murovanka",
                                                                    mapDisplayName="Мурованка")))
        self.assertEqual(info.map_name, "Murovanka")

    def test_parse_incomplete_and_bad(self):
        info = parse(make_replay(self.d / "b.wotreplay", results=None))
        self.assertFalse(info.complete)
        (self.d / "c.wotreplay").write_bytes(b"<html>not a replay</html>")
        with self.assertRaises(ReplayError):
            parse(self.d / "c.wotreplay")

    def test_versions(self):
        self.assertEqual(parse_version("World of Tanks v.1.26.0.1 #1234")[1], (1, 26, 0, 1))
        self.assertEqual(parse_version("1, 26, 0, 3")[0], "1.26.0.3")
        self.assertTrue(same_version((1, 26, 0, 1), (1, 26, 0)))
        self.assertFalse(same_version((1, 25, 1, 0), (1, 26, 0)))
        self.assertFalse(same_version((), (1, 26, 0)))

    def test_english_numbers(self):
        self.assertEqual(first_number("3,516"), 3516)
        self.assertEqual(first_number("180 ✕ Damage Caused ▼ FV215b 183"), 180)
        self.assertEqual(first_number("1 537"), 1537)


class DownloadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        good = make_replay(self.d / "good.bin").read_bytes()
        old = make_replay(self.d / "old.bin", first=dict(FIRST, clientVersionFromXml="1.25.1.0 #1")).read_bytes()
        weak = make_replay(self.d / "weak.bin", results=[dict(RESULTS[0], personal={"1": {"damageDealt": 900}})],
                           ).read_bytes()
        self.files = {"5000001": good, "5000002": old, "5000003": weak, "5000004": b"<html>404</html>",
                      "5000005": good}
        self.pages = {
            1: '<a href="/site/5000001#prokhorovka-someplayer-leopard_1">x</a> <a href="/site/download/5000001">d</a>'
               '<a href="/site/5000002#x">x</a><a href="/site/5000003#x">x</a>',
            2: '<a href="/site/5000004#x">x</a><a href="/site/5000005#x">x</a>',
            3: "<p>no replays</p>",
        }
        self.calls = []
        self.robots = None

    def tearDown(self):
        self.tmp.cleanup()

    def getter(self, url, timeout, referer=""):
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            if self.robots is None:
                raise rd.urllib.error.URLError("404")
            return self.robots.encode()
        if "/site/download/" in url:
            return self.files[url.rsplit("/", 1)[1]]
        n = int(rd.PAGE_RE.search(url).group(1)) if rd.PAGE_RE.search(url) else 1
        return self.pages.get(n, "").encode()

    def test_page_urls(self):
        u = "https://wotreplays.eu/site/index/version/123/tank/0/sort/uploaded_at.desc/"
        self.assertEqual(rd.page_url(u, 1), u)
        self.assertEqual(rd.page_url(u, 3), u + "page/3/")
        self.assertEqual(rd.page_url(u + "page/2/", 5), u + "page/5/")
        self.assertEqual(rd.replay_ids(self.pages[1]), ["5000001", "5000002", "5000003"])

    def test_fetch_filters_and_remembers(self):
        dest = self.d / "replays"
        opts = rd.DownloadOptions(url="https://wotreplays.eu/site/index/sort/uploaded_at.desc/", dest=dest, count=5,
                                  client_version=(1, 26, 0), min_damage=4000, pause_s=0)
        res = rd.fetch(opts, getter=self.getter)
        self.assertEqual(sorted(p.name for p in dest.glob("*.wotreplay")), ["5000001.wotreplay", "5000005.wotreplay"])
        self.assertEqual(len(res.saved), 2)
        self.assertIn("інша версія гри (1.25.1.0)", res.skipped)
        self.assertIn("шкода менша за 4000", res.skipped)
        self.assertTrue(any(k.startswith("не реплей") for k in res.skipped))
        self.assertTrue((dest / "5000001.json").exists())
        # повторний запуск — нічого не скачує повторно
        self.calls.clear()
        res2 = rd.fetch(opts, getter=self.getter)
        self.assertEqual(res2.saved, [])
        self.assertFalse(any("/download/" in c for c in self.calls))

    def test_robots_blocks(self):
        # як на wot-record.com: список і скачування заборонені для програм
        self.robots = "User-agent: *\nDisallow: /download/\nDisallow: /replays/\nDisallow: /replay/\n"
        opts = rd.DownloadOptions(url="https://wot-record.com/replays/date/desc?tier=10", dest=self.d / "r",
                                  pause_s=0)
        with self.assertRaises(rd.Blocked) as cm:
            rd.fetch(opts, getter=self.getter)
        self.assertIn("wot-record.com", str(cm.exception))
        self.assertEqual(self.calls, ["https://wot-record.com/robots.txt"])  # більше нічого не запитано
        self.robots = "User-agent: *\nDisallow: /profile/\n"
        rd.fetch(rd.DownloadOptions(url="https://wotreplays.eu/site/index/", dest=self.d / "r2", count=1, pause_s=0),
                 getter=self.getter)

    def test_query_paging_and_ids(self):
        u = "https://wot-record.com/replays/date/desc?tier=10&version=2.4"
        self.assertEqual(rd.page_url(u, 2), u + "&page=2")
        self.assertEqual(rd.page_url(u + "&page=2", 4), u + "&page=4")
        self.assertEqual(rd.page_url("https://wot-record.com/replays/date/desc", 3),
                         "https://wot-record.com/replays/date/desc?page=3")
        html = '<a class="tovabb" href="replay/447995/rock_n_roll_a_40tp"></a><a href="download/447995">d</a>'
        self.assertEqual(rd.replay_ids(html), ["447995"])
        self.assertEqual(rd.download_url("https://wot-record.com", "447995", u), "https://wot-record.com/download/447995")

    def test_import_folder(self):
        src, dest = self.d / "Downloads", self.d / "replays"
        src.mkdir()
        (src / "good.wotreplay").write_bytes(self.files["5000001"])
        (src / "old.wotreplay").write_bytes(self.files["5000002"])
        (src / "other.txt").write_text("x")
        res = rd.import_folder(rd.ImportOptions(src=src, dest=dest, client_version=(1, 26, 0), min_age_s=0))
        self.assertEqual([i.vehicle for i in res.saved], ["Leopard1"])
        self.assertTrue((dest / "good.wotreplay").exists() and (dest / "good.json").exists())
        self.assertFalse((src / "good.wotreplay").exists())
        self.assertTrue((src / "old.wotreplay").exists())   # непідхожий лишається на місці
        self.assertIn("інша версія гри (1.25.1.0)", res.skipped)

    def test_fetch_stops_at_count(self):
        opts = rd.DownloadOptions(url="https://wotreplays.eu/site/index/", dest=self.d / "r", count=1, pause_s=0)
        res = rd.fetch(opts, getter=self.getter)
        self.assertEqual(len(res.saved), 1)


class PosterHandoffTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_json_and_publish(self):
        info = parse(make_replay(self.d / "5000001.wotreplay"))
        opts = ReplayJobOptions(replays_dir=self.d, publish_dir=self.d / "ready_en")
        js = poster_json(info, opts)
        self.assertEqual(js["status"], "ready_to_post")
        self.assertEqual(js["youtube"]["title"], "Leopard1 — 7,412 damage on Prokhorovka 🔥")
        self.assertIn("Replay by SomePlayer.", js["youtube"]["description"])
        self.assertIn("Victory!", js["youtube"]["description"])
        no_credit = poster_json(info, ReplayJobOptions(replays_dir=self.d, publish_dir=None, credit_author=False))
        self.assertNotIn("SomePlayer", no_credit["youtube"]["description"])
        mp4 = self.d / "battle_01_dmg_7412.mp4"
        mp4.write_bytes(b"fake")
        out = publish(mp4, info, opts, "5000001")
        self.assertEqual(out.parent, self.d / "ready_en" / "WoT")
        self.assertTrue(out.exists() and out.with_suffix(".json").exists())
        self.assertFalse(list(out.parent.glob("*.part")))
        self.assertIsNone(publish(mp4, info, ReplayJobOptions(replays_dir=self.d, publish_dir=None), "x"))

    def test_pending(self):
        a = make_replay(self.d / "1.wotreplay")
        make_replay(self.d / "2.wotreplay")
        a.with_suffix(".recorded").write_text("x")
        self.assertEqual([p.name for p in pending_replays(self.d)], ["2.wotreplay"])


class FilterTest(unittest.TestCase):
    def test_build_url(self):
        from shorts.replay_filter import build_url, load_filters, tanks_for, version_value
        u = build_url({"tier": ["10"], "type": ["2", "3"], "version": ["2.4"], "map": ["11_murovanka"],
                       "nation": [], "tank": ["2865"]}, sort="view")
        self.assertEqual(u, "https://wot-record.com/replays/view/desc?tier=10&type=2,3&tank=2865&version=2.4"
                            "&map=11_murovanka")
        self.assertEqual(build_url({}), "https://wot-record.com/replays/date/desc")
        f = load_filters()
        self.assertGreater(len(f["tank"]), 500)
        self.assertEqual(len(f["nation"]), 11)
        polish10 = tanks_for([9], [10], [], filters=f)
        self.assertIn("60TP Lewandowskiego", [t["label"] for t in polish10])
        self.assertTrue(all(t["tier"] == 10 and t["nation"] == 9 for t in polish10))
        self.assertEqual([t["label"] for t in tanks_for(search="leopard 1", filters=f)][:1], ["Leopard 1"])
        self.assertEqual(version_value((2, 4, 1, 1)), "2.4")


class ImportSeenTest(unittest.TestCase):
    def test_unsuitable_checked_once(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            (d / "dl").mkdir()
            make_replay(d / "dl" / "weak.wotreplay")
            seen = set()
            opts = rd.ImportOptions(src=d / "dl", dest=d / "r", min_damage=9000, min_age_s=0, seen=seen)
            self.assertEqual(rd.import_folder(opts).skipped, {"шкода менша за 9000": 1})
            self.assertEqual(rd.import_folder(opts).skipped, {})      # вдруге не перевіряється
            self.assertTrue((d / "dl" / "weak.wotreplay").exists())


if __name__ == "__main__":
    unittest.main()
