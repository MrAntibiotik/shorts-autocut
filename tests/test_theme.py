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
        lines = b.lines(now=310)
        self.assertEqual(len(lines), 2 * (len(STAGES) + 1))
        self.assertTrue(lines[0][0].startswith("✓ ЗАПИС 1/2"))
        self.assertIn("[ >> ]", "".join(t for t, tag in lines if tag == "run"))
        b.finish(False, now=320)
        self.assertIn("fail", [tag for _t, tag in b.lines(now=330)])

    def test_bar(self):
        self.assertEqual(bar(0.5, 10), "█████░░░░░")
        self.assertEqual(bar(2, 4), "████")


if __name__ == "__main__":
    unittest.main()
