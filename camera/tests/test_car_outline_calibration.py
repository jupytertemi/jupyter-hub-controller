"""Backend tests for the car-outline calibration step (Step E.5).

Spec: jupyter-helios-web/docs/PR-spec-vehicle-ai-car-outline.md §5.2 (H1-H5)

Run via:
    DJANGO_SETTINGS_MODULE=hub_controller.settings.local \
        python manage.py test camera.tests.test_car_outline_calibration
"""
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from camera.models import Camera, CameraSetting


def _full_payload(**overrides):
    """Default valid payload with detection_zone + arrow + park + car_outline.
    Mirrors what the Flutter wizard POSTs at the end of Step F (validation).
    """
    base = {
        "entry_point_x": 0.18,
        "entry_point_y": 0.42,
        "approach_angle_deg": 295.0,
        "park_polygon": [
            [0.42, 0.55],
            [0.78, 0.55],
            [0.78, 0.92],
            [0.42, 0.92],
        ],
        "detection_zone": [
            [0.10, 0.10],
            [0.90, 0.10],
            [0.90, 0.95],
            [0.10, 0.95],
        ],
        "car_outline": [
            [0.35, 0.45],
            [0.65, 0.45],
            [0.65, 0.70],
            [0.35, 0.70],
        ],
        "plate_readability_px": 96.0,
        "plate_ocr_skip": False,
    }
    base.update(overrides)
    return base


class CarOutlineCalibrationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.camera = Camera.objects.create(
            name="Test Camera",
            slug_name="car-outline-test-001",
            ip="192.168.1.99",
        )
        CameraSetting.objects.create(
            license_vehicle_recognition=True,
            vehicle_recognition_camera=self.camera,
        )
        self.url = f"/api/cameras/{self.camera.slug_name}/vehicle-calibration"

    # ---- H1: POST with valid car_outline → 200, persisted ----
    def test_h1_post_valid_persists_all_fields(self):
        payload = _full_payload()
        resp = self.client.post(self.url, payload, format="json")
        self.assertIn(resp.status_code, (200, 201), resp.content)

        self.camera.refresh_from_db()
        self.assertEqual(self.camera.vehicle_car_outline, payload["car_outline"])
        self.assertEqual(self.camera.vehicle_plate_readability_px, 96.0)
        self.assertFalse(self.camera.vehicle_plate_ocr_skip)

    # ---- H2: POST with 3 corners → 400 ----
    def test_h2_post_three_corners_returns_400(self):
        payload = _full_payload(car_outline=[
            [0.35, 0.45],
            [0.65, 0.45],
            [0.65, 0.70],
        ])
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    # ---- H3: corners outside [0,1] → 400 ----
    def test_h3_post_corner_out_of_range_returns_400(self):
        payload = _full_payload(car_outline=[
            [1.50, 0.45],   # x > 1.0 — invalid
            [0.65, 0.45],
            [0.65, 0.70],
            [0.35, 0.70],
        ])
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_h3b_post_negative_readability_returns_400(self):
        payload = _full_payload(plate_readability_px=-5.0)
        resp = self.client.post(self.url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    # ---- H4: GET returns the new fields ----
    def test_h4_get_returns_new_fields(self):
        # Pre-populate via POST first
        self.client.post(self.url, _full_payload(plate_ocr_skip=True), format="json")

        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        body = resp.json()
        self.assertIn("car_outline", body)
        self.assertIn("plate_readability_px", body)
        self.assertIn("plate_ocr_skip", body)
        self.assertEqual(len(body["car_outline"]), 4)
        self.assertEqual(body["plate_readability_px"], 96.0)
        self.assertTrue(body["plate_ocr_skip"])

    # ---- H5: pre-existing camera (no car_outline) reads as null, no crash ----
    def test_h5_pre_existing_camera_no_car_outline_reads_clean(self):
        # Set only the legacy fields — no car_outline
        legacy_payload = {
            "entry_point_x": 0.18,
            "entry_point_y": 0.42,
            "approach_angle_deg": 295.0,
            "park_polygon": _full_payload()["park_polygon"],
            "detection_zone": _full_payload()["detection_zone"],
        }
        post_resp = self.client.post(self.url, legacy_payload, format="json")
        self.assertIn(post_resp.status_code, (200, 201))

        # GET must succeed and the new fields are null/false defaults
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        body = resp.json()
        self.assertIsNone(body.get("car_outline"))
        self.assertIsNone(body.get("plate_readability_px"))
        self.assertFalse(body.get("plate_ocr_skip"))

    # ---- bonus: plate_ocr_skip toggling preserved on partial-update ----
    def test_plate_ocr_skip_persists_after_set_true(self):
        self.client.post(self.url, _full_payload(plate_ocr_skip=True), format="json")
        self.camera.refresh_from_db()
        self.assertTrue(self.camera.vehicle_plate_ocr_skip)

        # POST again with same fields but no plate_ocr_skip override
        # → should leave it untouched (PATCH semantics).
        payload = _full_payload()
        payload.pop("plate_ocr_skip")
        self.client.post(self.url, payload, format="json")
        self.camera.refresh_from_db()
        # Field absent from body → existing value preserved
        self.assertTrue(self.camera.vehicle_plate_ocr_skip)
