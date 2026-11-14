"""Заклик у шортсі: переноситься на рядки й ніколи не виходить за кадр."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shorts.config import load_settings  # noqa: E402
from shorts.textimg import cta_image, render_text, wrap  # noqa: E402


class CtaTest(unittest.TestCase):
    def test_long_cta_wraps_and_fits(self):
        st = load_settings()
        for text in ("Day 3 · Ukrainian veteran on the road to 1000 subs — subscribe 🛡️♥️",
                     "Subscribe" * 12, "Коротко"):
            img = cta_image(text, st, 1080)
            self.assertLessEqual(img.width, 1080 - 2 * 70, text)          # з відступами від країв
        lines = wrap("Day 3 · Ukrainian veteran on the road to 1000 subs — subscribe", st["subtitles"], 76, 900)
        self.assertGreater(len(lines), 1)
        img = render_text("один дуже-дуже-довгий-рядок-без-пробілів-який-не-переноситься" * 2, st["subtitles"],
                          font_size=76, max_width=600)
        self.assertLessEqual(img.width, 600)

    def test_replay_profile_cta_higher(self):
        import json
        prof = json.loads((ROOT / "config" / "profiles" / "wot_replay_en.json").read_text(encoding="utf-8"))
        self.assertEqual(prof["layout"]["cta_y"], 1684)                    # на 5% кадру вище за 1780


if __name__ == "__main__":
    unittest.main()
