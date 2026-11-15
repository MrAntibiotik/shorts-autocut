"""Засвіт не плутається із заблокованою шкодою, коли рядки блоку лічильників зсунуті."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shorts.vision_detector import add_battle_stats, full_counters  # noqa: E402


def rows(n, **raw):
    return [dict({"t": float(i)}, **raw) for i in range(n)]


class AssistLayoutTest(unittest.TestCase):
    def test_two_rows_do_not_count_as_assist(self):
        # у бою немає засвіту: видно лише два рядки, і в «третьому» опинилась заблокована шкода
        b = {"start": 0, "stop": 20, "end": 20, "damage_counter": 5717}
        add_battle_stats([b], rows(10, dmg_raw="", blk_raw="5717", ast_raw="4411"))
        self.assertIsNone(b["assist_total"])
        self.assertEqual(b["damage_total"], 5717)

    def test_full_layout_keeps_assist(self):
        b = {"start": 0, "stop": 20, "end": 20, "damage_counter": 3000}
        add_battle_stats([b], rows(10, dmg_raw="3000", blk_raw="1200", ast_raw="2500"))
        self.assertEqual(b["assist_total"], 2500)

    def test_full_counters(self):
        self.assertTrue(full_counters({"dmg_raw": "1", "blk_raw": "2", "ast_raw": "3"}))
        self.assertFalse(full_counters({"dmg_raw": "", "blk_raw": "2", "ast_raw": "3"}))


if __name__ == "__main__":
    unittest.main()
