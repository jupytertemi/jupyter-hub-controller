"""Pure-Python validation tests for the VehicleCalibrationSerializer.

Runs without Django integration (uses DRF's serializer only). For full HTTP
round-trip coverage, see test_vehicle_calibration_view.py.

Run via:
    DJANGO_SETTINGS_MODULE=hub_controller.settings.local \
        python manage.py test camera.tests.test_vehicle_calibration_serializer
"""
from django.test import SimpleTestCase

from camera.serializers import VehicleCalibrationSerializer


def _valid_payload(**overrides):
    """Default valid payload for spec-compliant calibration."""
    base = {
        "entry_point_x": 0.18,
        "entry_point_y": 0.42,
        "approach_angle_deg": 295.0,
        "park_polygon": [
            [0.42, 0.55],  # TL
            [0.78, 0.55],  # TR
            [0.78, 0.92],  # BR
            [0.42, 0.92],  # BL
        ],
    }
    base.update(overrides)
    return base


class CoordinateRangeTests(SimpleTestCase):
    """Each scalar coordinate must be in [0, 1]; angle in [0, 360)."""

    def test_valid_payload_passes(self):
        s = VehicleCalibrationSerializer(data=_valid_payload())
        self.assertTrue(s.is_valid(), msg=s.errors)

    def test_entry_point_x_negative_rejected(self):
        s = VehicleCalibrationSerializer(data=_valid_payload(entry_point_x=-0.01))
        self.assertFalse(s.is_valid())
        self.assertIn("entry_point_x", s.errors)

    def test_entry_point_x_above_one_rejected(self):
        s = VehicleCalibrationSerializer(data=_valid_payload(entry_point_x=1.01))
        self.assertFalse(s.is_valid())
        self.assertIn("entry_point_x", s.errors)

    def test_entry_point_y_at_zero_accepted(self):
        s = VehicleCalibrationSerializer(data=_valid_payload(entry_point_y=0.0))
        self.assertTrue(s.is_valid(), msg=s.errors)

    def test_entry_point_y_at_one_accepted(self):
        # x=1.0 edge of frame, must work as the user could legitimately pin there.
        s = VehicleCalibrationSerializer(data=_valid_payload(
            entry_point_x=1.0, entry_point_y=0.0))
        self.assertTrue(s.is_valid(), msg=s.errors)

    def test_angle_360_rejected(self):
        s = VehicleCalibrationSerializer(data=_valid_payload(approach_angle_deg=360.0))
        self.assertFalse(s.is_valid())
        self.assertIn("approach_angle_deg", s.errors)

    def test_angle_359_99_accepted(self):
        s = VehicleCalibrationSerializer(data=_valid_payload(approach_angle_deg=359.99))
        self.assertTrue(s.is_valid(), msg=s.errors)

    def test_angle_negative_rejected(self):
        s = VehicleCalibrationSerializer(data=_valid_payload(approach_angle_deg=-1.0))
        self.assertFalse(s.is_valid())
        self.assertIn("approach_angle_deg", s.errors)

    def test_angle_zero_accepted(self):
        s = VehicleCalibrationSerializer(data=_valid_payload(approach_angle_deg=0.0))
        self.assertTrue(s.is_valid(), msg=s.errors)


class ParkPolygonShapeTests(SimpleTestCase):
    """park_polygon must be exactly 4 [x,y] pairs, axis-aligned, non-degenerate."""

    def test_three_corners_rejected(self):
        s = VehicleCalibrationSerializer(data=_valid_payload(
            park_polygon=[[0.4, 0.5], [0.7, 0.5], [0.7, 0.9]]))
        self.assertFalse(s.is_valid())
        self.assertIn("park_polygon", s.errors)

    def test_five_corners_rejected(self):
        s = VehicleCalibrationSerializer(data=_valid_payload(
            park_polygon=[[0.4, 0.5], [0.7, 0.5], [0.7, 0.9], [0.4, 0.9], [0.5, 0.7]]))
        self.assertFalse(s.is_valid())
        self.assertIn("park_polygon", s.errors)

    def test_corner_coord_above_one_rejected(self):
        s = VehicleCalibrationSerializer(data=_valid_payload(
            park_polygon=[[0.4, 0.5], [1.5, 0.5], [1.5, 0.9], [0.4, 0.9]]))
        self.assertFalse(s.is_valid())
        self.assertIn("park_polygon", s.errors)

    def test_non_axis_aligned_rejected_TL_BL_x(self):
        # TL.x ≠ BL.x  → not axis-aligned
        s = VehicleCalibrationSerializer(data=_valid_payload(
            park_polygon=[[0.42, 0.55], [0.78, 0.55], [0.78, 0.92], [0.50, 0.92]]))
        self.assertFalse(s.is_valid())
        self.assertIn("park_polygon", s.errors)

    def test_non_axis_aligned_rejected_TR_BR_x(self):
        s = VehicleCalibrationSerializer(data=_valid_payload(
            park_polygon=[[0.42, 0.55], [0.78, 0.55], [0.70, 0.92], [0.42, 0.92]]))
        self.assertFalse(s.is_valid())
        self.assertIn("park_polygon", s.errors)

    def test_non_axis_aligned_rejected_TL_TR_y(self):
        s = VehicleCalibrationSerializer(data=_valid_payload(
            park_polygon=[[0.42, 0.55], [0.78, 0.60], [0.78, 0.92], [0.42, 0.92]]))
        self.assertFalse(s.is_valid())
        self.assertIn("park_polygon", s.errors)

    def test_non_axis_aligned_rejected_BL_BR_y(self):
        s = VehicleCalibrationSerializer(data=_valid_payload(
            park_polygon=[[0.42, 0.55], [0.78, 0.55], [0.78, 0.92], [0.42, 0.85]]))
        self.assertFalse(s.is_valid())
        self.assertIn("park_polygon", s.errors)

    def test_zero_width_rejected(self):
        # TL.x == TR.x (zero-width rectangle) — geometrically degenerate
        s = VehicleCalibrationSerializer(data=_valid_payload(
            park_polygon=[[0.5, 0.55], [0.5, 0.55], [0.5, 0.92], [0.5, 0.92]]))
        self.assertFalse(s.is_valid())
        self.assertIn("park_polygon", s.errors)

    def test_zero_height_rejected(self):
        s = VehicleCalibrationSerializer(data=_valid_payload(
            park_polygon=[[0.42, 0.55], [0.78, 0.55], [0.78, 0.55], [0.42, 0.55]]))
        self.assertFalse(s.is_valid())
        self.assertIn("park_polygon", s.errors)

    def test_inverted_corners_rejected(self):
        # TR.x < TL.x → negative width (corners swapped)
        s = VehicleCalibrationSerializer(data=_valid_payload(
            park_polygon=[[0.78, 0.55], [0.42, 0.55], [0.42, 0.92], [0.78, 0.92]]))
        self.assertFalse(s.is_valid())
        self.assertIn("park_polygon", s.errors)


class CrossFieldTests(SimpleTestCase):
    """entry_point must NOT lie inside the park rectangle."""

    def test_entry_inside_park_rejected(self):
        # entry (0.5, 0.7) lies inside park rect [0.4..0.78, 0.55..0.92]
        s = VehicleCalibrationSerializer(data=_valid_payload(
            entry_point_x=0.5, entry_point_y=0.7))
        self.assertFalse(s.is_valid())
        self.assertIn("non_field_errors", s.errors)

    def test_entry_on_park_edge_rejected(self):
        # entry exactly on edge counts as inside (collapsed boundary)
        s = VehicleCalibrationSerializer(data=_valid_payload(
            entry_point_x=0.42, entry_point_y=0.55))
        self.assertFalse(s.is_valid())
        self.assertIn("non_field_errors", s.errors)

    def test_entry_far_from_park_accepted(self):
        s = VehicleCalibrationSerializer(data=_valid_payload(
            entry_point_x=0.10, entry_point_y=0.20))
        self.assertTrue(s.is_valid(), msg=s.errors)

    def test_entry_just_outside_park_left_accepted(self):
        # entry at x=0.41 is just outside park_polygon's left edge x=0.42
        s = VehicleCalibrationSerializer(data=_valid_payload(
            entry_point_x=0.41, entry_point_y=0.70))
        self.assertTrue(s.is_valid(), msg=s.errors)


class FieldMissingTests(SimpleTestCase):
    """All four top-level fields are required."""

    def test_missing_entry_point_x(self):
        payload = _valid_payload()
        del payload["entry_point_x"]
        s = VehicleCalibrationSerializer(data=payload)
        self.assertFalse(s.is_valid())
        self.assertIn("entry_point_x", s.errors)

    def test_missing_park_polygon(self):
        payload = _valid_payload()
        del payload["park_polygon"]
        s = VehicleCalibrationSerializer(data=payload)
        self.assertFalse(s.is_valid())
        self.assertIn("park_polygon", s.errors)

    def test_missing_approach_angle(self):
        payload = _valid_payload()
        del payload["approach_angle_deg"]
        s = VehicleCalibrationSerializer(data=payload)
        self.assertFalse(s.is_valid())
        self.assertIn("approach_angle_deg", s.errors)
