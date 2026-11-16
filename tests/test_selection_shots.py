"""Довгий бій: усі постріли в шортсі — ранні коротко («вистріл + шкода»), останні 2–3 — з повним запасом."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shorts import events as E  # noqa: E402
from shorts.events import Event  # noqa: E402
from shorts.selection import select_battle  # noqa: E402

CFG = {
    "max_duration_s": 59.5, "pre_s": [3.0, 3.0], "post_s": [6.0, 6.0], "merge_gap_s": 4.0,
    "min_score": 1.0, "always_include_final": True, "speech_snap": False, "compact_pre_s": 1.5,
    "compact_post_s": 3.5, "confirmed_first": True, "short_early_shots": True, "tail_full_shots": 3,
    "shot_pre_s": 2.0, "shot_post_s": 1.5, "death_pre_s": 4.0, "death_post_s": 1.5,
}


def hit(t, score=2.0):
    return Event(t=t, type=E.HIT, source="лічильник шкоди", value=300, extra={"moment": True, "score": score})


class ShotsTest(unittest.TestCase):
    def battle(self, n_shots, step=25.0):
        evs = [hit(30 + i * step, score=1.0 + (i % 3)) for i in range(n_shots)]
        last = evs[-1].t
        evs.append(Event(t=last + 20, type=E.DEATH, source="екран", extra={"moment": True, "score": 3.0}))
        return {"index": 1, "events": evs}, evs[:-1], evs[-1]

    def test_all_shots_fit_short_early_full_tail(self):
        b, shots, death = self.battle(12)   # 12 пострілів: з повним (9 с) і навіть коротким (5 с) запасом — не влазять
        segs, final = select_battle(b, CFG, 0, 10_000, None)
        total = sum(s.end - s.start for s in segs)
        self.assertLessEqual(total, 59.5 + 1e-6)
        covered = [e for e in shots if any(s.start <= e.t <= s.end for s in segs)]
        self.assertEqual(len(covered), 12)                         # жоден постріл не загубився
        self.assertTrue(any(s.start <= death.t <= s.end for s in segs))  # фінал бою є
        for e in shots[-2:]:                                       # останні — з повним запасом (3 с до, 6 с після)
            seg = next(s for s in segs if s.start <= e.t <= s.end)
            self.assertLessEqual(seg.start, e.t - 3.0 + 1e-6)
            self.assertGreaterEqual(seg.end, e.t + 6.0 - 1e-6)
        for e in shots[:-2]:                                       # ранні — коротко: вистріл + напис шкоди
            seg = next(s for s in segs if s.start <= e.t <= s.end)
            self.assertAlmostEqual(seg.end - seg.start, 3.5, delta=0.01)

    def test_too_many_drops_weakest_early_not_the_end(self):
        b, shots, death = self.battle(20)   # 20 пострілів — і коротко не влазять усі
        segs, _ = select_battle(b, CFG, 0, 10_000, None)
        self.assertLessEqual(sum(s.end - s.start for s in segs), 59.5 + 1e-6)
        covered = {e.t for e in shots if any(s.start <= e.t <= s.end for s in segs)}
        for e in shots[-2:]:
            self.assertIn(e.t, covered)                            # кінець бою не викидається
        self.assertGreater(len(covered), 10)

    def test_short_battle_unchanged(self):
        b, shots, _ = self.battle(3)        # мало пострілів — як раніше, повний запас для всіх
        segs, _ = select_battle(b, CFG, 0, 10_000, None)
        for e in shots:
            seg = next(s for s in segs if s.start <= e.t <= s.end)
            self.assertLessEqual(seg.start, e.t - 3.0 + 1e-6)
