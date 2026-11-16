"""Шортс не довший за 58 с — за будь-яких налаштувань профілю."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shorts.selection import SHORT_MAX_S, Segment, cap_segments, shorts_limit  # noqa: E402


class ShortLimitTest(unittest.TestCase):
    def test_limit_never_above_58(self):
        self.assertEqual(SHORT_MAX_S, 58.0)
        self.assertEqual(shorts_limit({"max_duration_s": 59.5}), 58.0)     # старий профіль із 59,5
        self.assertEqual(shorts_limit({"max_duration_s": 45}), 45)

    def test_cap_trims_from_the_end(self):
        segs = [Segment(0, 30), Segment(100, 125), Segment(200, 210)]      # 65 с
        out = cap_segments(segs)
        self.assertAlmostEqual(sum(s.duration for s in out), 58.0)
        self.assertEqual((out[0].start, out[0].end, out[1].start, out[1].end), (0, 30, 100, 125))
        self.assertAlmostEqual(out[2].end, 203.0)
        short = [Segment(0, 20)]
        self.assertIs(cap_segments(short), short)                          # коротші — без змін

    def test_ffmpeg_hard_stop_for_vertical(self):
        import json
        from shorts.layout import compute_layout
        from shorts.media import VideoInfo
        from shorts.render import build_command
        prof = json.loads((ROOT / "config" / "profiles" / "wot_replay_en.json").read_text(encoding="utf-8"))
        self.assertLessEqual(prof["selection"]["max_duration_s"], 58.0)
        lay = compute_layout(prof, 1920, 1080)
        info = VideoInfo(path=Path("rec.mkv"), width=1920, height=1080, fps=60.0, duration=600.0, audio_streams=1,
                         video_codec="h264")
        from shorts.config import load_settings
        from unittest import mock
        with mock.patch("shorts.render.find_tool", return_value="ffmpeg"):
            cmd = build_command(load_settings(), info, lay, [(10.0, 70.0)], [], Path("/tmp/x.mp4"), None, "libx264")
        last_t = len(cmd) - 1 - cmd[::-1].index("-t")                      # вихідний -t (не обрізка входу)
        self.assertEqual(cmd[last_t + 1], "58.000")
        self.assertGreater(last_t, cmd.index("-filter_complex"))


if __name__ == "__main__":
    unittest.main()
