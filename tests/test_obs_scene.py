import json
import tempfile
import unittest
from pathlib import Path

from autocut.core.obs_scene import apply_to_webcam, webcam_crop


def collection(item_crop=(0, 0, 0, 0), crop_filter=None, filter_before=False):
    filters = [{"id": "source_record_filter", "name": "Source Record", "settings": {}}]
    if crop_filter:
        f = {"id": "crop_filter", "name": "Crop", "enabled": True,
             "settings": dict(zip(("left", "top", "right", "bottom"), crop_filter))}
        filters = [f] + filters if filter_before else filters + [f]
    l, t, r, b = item_crop
    return {
        "current_scene": "Гра",
        "sources": [
            {"id": "dshow_input", "name": "Вебка", "filters": filters, "settings": {}},
            {"id": "game_capture", "name": "WoT", "settings": {}},
            {"id": "scene", "name": "Камера", "settings": {"items": [
                {"name": "Вебка", "visible": True, "crop_left": l, "crop_top": t, "crop_right": r, "crop_bottom": b}]}},
            {"id": "scene", "name": "Гра", "settings": {"items": [
                {"name": "WoT", "visible": True}, {"name": "Камера", "visible": True}]}},
        ],
    }


class ObsCropTest(unittest.TestCase):
    def crop(self, data, w=1920, h=1080):
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "Стрім.json").write_text(json.dumps(data), encoding="utf-8")
            got = webcam_crop(w, h, Path(t))
        return got[0] if got else None

    def test_scene_item_crop_in_nested_scene(self):
        # праворуч прибрано полиці (640 пкс), зверху 120 пкс
        self.assertEqual(self.crop(collection((0, 120, 640, 0))), [0.0, 0.1111, 0.6667, 0.8889])

    def test_crop_filter_after_source_record_counts(self):
        self.assertEqual(self.crop(collection((0, 0, 0, 0), crop_filter=(192, 0, 192, 0))), [0.1, 0.0, 0.8, 1.0])

    def test_crop_filter_before_source_record_is_already_in_file(self):
        self.assertIsNone(self.crop(collection((0, 0, 0, 0), crop_filter=(192, 0, 192, 0), filter_before=True)))

    def test_no_crop_no_obs(self):
        self.assertIsNone(self.crop(collection()))
        self.assertIsNone(webcam_crop(1920, 1080, Path("/немає/такої/папки")))

    def test_manual_crop_wins(self):
        cam = {"width": 1920, "height": 1080}
        apply_to_webcam(cam, {"zones": {"webcam_file": [0.1, 0.1, 0.5, 0.5]}})
        self.assertNotIn("crop", cam)

    def test_layout_uses_obs_crop(self):
        from autocut.core.config import load_profile
        from autocut.core.layout import compute_layout
        prof = load_profile("wot")
        prof.setdefault("zones", {}).pop("webcam_file", None)
        full = {"path": "w.mkv", "width": 1920, "height": 1080, "fps": 30, "offset": 0}
        cut = dict(full, crop=[0.0, 0.0, 0.5, 1.0])
        lw = next(l for l in compute_layout(prof, 3440, 1440, full).layers if l.name == "webcam")
        lc = next(l for l in compute_layout(prof, 3440, 1440, cut).layers if l.name == "webcam")
        self.assertLessEqual(lc.src.x + lc.src.w, 960)                   # лише ліва половина кадру камери
        self.assertGreater(lw.src.x + lw.src.w, 960)


if __name__ == "__main__":
    unittest.main()


class HighlightTitleTest(unittest.TestCase):
    def test_bigger_stat_in_title(self):
        from autocut.main.highlights import poster_texts
        self.assertTrue(poster_texts({}, {"damage_total": 7412, "assist_total": 1200})["title"].startswith("7 412 шкоди — "))
        t = poster_texts({}, {"damage_total": 900, "assist_total": 6133})["title"]
        self.assertTrue(t.startswith("6 133 засвіту — "))
        self.assertNotIn("900", t)
