import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from eye_cropper import EyeCropper


class EyeCropperTests(unittest.TestCase):
    def test_selects_leftmost_and_rightmost_eye_boxes(self):
        detections = [
            (300, 90, 60, 40),
            (100, 95, 55, 38),
            (205, 20, 20, 20),
        ]

        self.assertEqual(EyeCropper._select_eye_box(detections, "left"), (100, 95, 55, 38))
        self.assertEqual(EyeCropper._select_eye_box(detections, "right"), (300, 90, 60, 40))

    def test_expanded_square_box_stays_inside_frame(self):
        box = EyeCropper._expand_to_square(
            (5, 8, 40, 20),
            frame_width=100,
            frame_height=80,
            padding=0.5,
        )

        x, y, width, height = box
        self.assertEqual(width, height)
        self.assertGreater(width, 40)
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)
        self.assertLessEqual(x + width, 100)
        self.assertLessEqual(y + height, 80)

    def test_dedupes_overlapping_detections(self):
        boxes = EyeCropper._dedupe_boxes([
            (100, 100, 50, 30),
            (104, 102, 48, 29),
            (250, 100, 50, 30),
        ])

        self.assertEqual(len(boxes), 2)

    def test_selects_largest_detection_inside_one_search_half(self):
        detections = [
            (30, 80, 20, 20),
            (120, 90, 60, 35),
            (220, 95, 30, 25),
        ]

        self.assertEqual(EyeCropper._select_best_eye_box(detections), (120, 90, 60, 35))

    def test_normalized_search_roi_converts_to_pixel_region(self):
        roi = EyeCropper._normalized_to_pixel_roi(
            (0.5, 0.0, 0.5, 1.0),
            frame_width=640,
            frame_height=480,
        )

        self.assertEqual(roi, (320, 0, 320, 480))


if __name__ == "__main__":
    unittest.main()
