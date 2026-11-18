import unittest

from autocut.app.theme import STAGES, StageBoard, bar


class StageBoardTest(unittest.TestCase):
    def test_stages_progress_skip_and_finish(self):
        b = StageBoard(["a.mkv", "b.mkv"])
        b.update("[1/2] Аналіз запису", 0.0, now=100)
        b.update("[1/2] Розпізнавання екрану (лічильники, повідомлення про шкоду, таймер)", 0.005 + 0.11, now=110)
        self.assertEqual(b.status[(0, 0)], "ok")
        self.assertEqual(b.status[(0, 1)], "run")
        self.assertAlmostEqual(b.local, 0.5, places=2)                    # половина етапу «скан екрану»
        b.update("[1/2] Рендер MP4", 0.45, now=200)                       # субтитри вимкнені — пропущено
        self.assertEqual(b.status[(0, 5)], "skip")
        b.update("[2/2] Аналіз запису", 0.5, now=300)
        self.assertEqual(b.status[(0, 7)], "skip")                         # «звіт» не був — пропущено
        rows = b.rows(now=310)
        self.assertEqual(len(rows), 2 * (len(STAGES) + 1))
        self.assertEqual((rows[0]["kind"], rows[0]["state"]), ("head", "ok"))       # запис 1 — готово
        run = [r for r in rows if r["kind"] == "stage" and r["state"] == "run"]
        self.assertEqual(len(run), 1)
        self.assertEqual(run[0]["time"], "00:10")
        b.finish(False, now=320)
        self.assertIn("fail", [r["state"] for r in b.rows(now=330)])

    def test_bar(self):
        self.assertEqual(bar(0.5, 10), "█████░░░░░")
        self.assertEqual(bar(2, 4), "████")


if __name__ == "__main__":
    unittest.main()
