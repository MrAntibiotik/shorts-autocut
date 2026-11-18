"""EU (реплеї): моменти не влазять у 59,5 с — не викидаємо, а пришвидшуємо шортс рівно до ліміту."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from autocut.core import events as E  # noqa: E402
from autocut.core.events import Event  # noqa: E402
from autocut.core.selection import SHORT_MAX_S, select_battle, select_shorts  # noqa: E402
from tests.test_selection_shots import CFG, hit  # noqa: E402

SPEED = dict(CFG, max_duration_s=59.5, speed_fit=True, speed_max=2.0, min_battle_score=0, min_short_s=1, min_hits=0,
             max_shorts=0)


def battle(n, step=25.0):
    evs = [hit(30 + i * step, score=1.0 + (i % 3)) for i in range(n)]
    evs.append(Event(t=evs[-1].t + 20, type=E.DEATH, source="екран", extra={"moment": True, "score": 3.0}))
    return {"index": 1, "events": evs, "damage_total": 9000}, evs[:-1]


class SpeedFitTest(unittest.TestCase):
    def test_all_moments_full_margins_then_speed_up(self):
        b, shots = battle(9)                       # 9 пострілів × 9 с + фінал ≈ 85 с — у 59,5 не влазить
        segs, _ = select_battle(b, SPEED, 0, 10_000, None)
        covered = [e for e in shots if any(s.start <= e.t <= s.end for s in segs)]
        self.assertEqual(len(covered), 9)                                  # нічого не викинуто
        for e in shots:                                                    # і запас повний (3 с до, 6 с після)
            seg = next(s for s in segs if s.start <= e.t <= s.end)
            self.assertLessEqual(seg.start, e.t - 3.0 + 1e-6)
            self.assertGreaterEqual(seg.end, e.t + 6.0 - 1e-6)
        sh = select_shorts([b], {"selection": SPEED}, 0, 10_000)[0]
        self.assertGreater(sh.speed, 1.0)
        self.assertLessEqual(sh.speed, 2.0)
        self.assertAlmostEqual(sh.out_duration, SHORT_MAX_S, places=2)    # рівно до ліміту
        self.assertAlmostEqual(sh.speed, sh.duration / SHORT_MAX_S, places=3)

    def test_short_battle_not_sped_up(self):
        b, _ = battle(3)
        sh = select_shorts([b], {"selection": SPEED}, 0, 10_000)[0]
        self.assertEqual(sh.speed, 1.0)

    def test_my_channel_unchanged(self):
        b, shots = battle(9)
        sh = select_shorts([b], {"selection": dict(SPEED, speed_fit=False)}, 0, 10_000)[0]
        self.assertEqual(sh.speed, 1.0)
        self.assertLessEqual(sh.duration, SHORT_MAX_S + 1e-6)

    def test_render_command_speeds_video_and_audio(self):
        from unittest import mock

        from autocut.core.config import load_profile, load_settings
        from autocut.core.layout import compute_layout
        from autocut.core.media import VideoInfo
        from autocut.core.render import _atempo, build_command
        lay = compute_layout(load_profile("wot_replay_en"), 1920, 1080)
        info = VideoInfo(path=Path("rec.mkv"), width=1920, height=1080, fps=60.0, duration=600.0, audio_streams=1,
                         video_codec="h264")
        with mock.patch("autocut.core.render.find_tool", return_value="ffmpeg"):
            cmd = build_command(load_settings(), info, lay, [(10.0, 50.0), (100.0, 140.0)], [], Path("x.mp4"), None,
                                "libx264", speed=80 / 59.5)
        g = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("setpts=PTS/1.34454", g)
        self.assertIn("atempo=1.34454", g)
        self.assertEqual(cmd[cmd.index("-map", cmd.index("-map") + 1) + 1], "[asp]")
        self.assertEqual(_atempo(3.0), "atempo=2.0,atempo=1.50000")      # понад ×2 — ланцюжком


if __name__ == "__main__":
    unittest.main()
