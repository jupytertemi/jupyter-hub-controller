"""Integration tests for CameraVehicleCalibrationView.

Covers GET / POST / DELETE round-trip with full Django stack: ORM, URL routing,
auth (or its absence), serializer round-trip through DRF.

Run via:
    DJANGO_SETTINGS_MODULE=hub_controller.settings.local \
        python manage.py test camera.tests.test_vehicle_calibration_view
"""
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from camera.models import Camera, CameraSetting


def _valid_payload(**overrides):
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
    }
    base.update(overrides)
    return base


class VehicleCalibrationViewTests(TestCase):
    """End-to-end tests through the view layer."""

    def setUp(self):
        self.client = APIClient()
        self.camera = Camera.objects.create(
            name="Test Camera",
            slug_name="test-camera-001",
            ip="192.168.1.99",
        )
        # Enable VehicleAI and target this camera.
        self.setting = CameraSetting.objects.create(
            license_vehicle_recognition=True,
            vehicle_recognition_camera=self.camera,
        )

    def _url(self, slug=None):
        return reverse(
            "camera:camera-vehicle-calibration",
            kwargs={"slug": slug or self.camera.slug_name},
        )

    # ---------- GET ----------

    def test_get_unset_returns_404(self):
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_get_unknown_camera_returns_404(self):
        resp = self.client.get(self._url(slug="does-not-exist"))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    # ---------- POST happy path ----------

    def test_post_valid_payload_persists(self):
        resp = self.client.post(self._url(), data=_valid_payload(), format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK, msg=resp.data)
        self.camera.refresh_from_db()
        self.assertAlmostEqual(self.camera.vehicle_entry_point_x, 0.18)
        self.assertAlmostEqual(self.camera.vehicle_entry_point_y, 0.42)
        self.assertAlmostEqual(self.camera.vehicle_approach_angle_deg, 295.0)
        self.assertEqual(len(self.camera.vehicle_park_polygon), 4)

    def test_post_then_get_round_trip(self):
        payload = _valid_payload()
        self.client.post(self._url(), data=payload, format="json")
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertAlmostEqual(resp.data["entry_point_x"], payload["entry_point_x"])
        self.assertAlmostEqual(resp.data["approach_angle_deg"], payload["approach_angle_deg"])
        self.assertEqual(resp.data["park_polygon"], payload["park_polygon"])

    def test_post_overwrites_existing(self):
        self.client.post(self._url(), data=_valid_payload(), format="json")
        new_payload = _valid_payload(approach_angle_deg=120.0)
        self.client.post(self._url(), data=new_payload, format="json")
        self.camera.refresh_from_db()
        self.assertAlmostEqual(self.camera.vehicle_approach_angle_deg, 120.0)

    # ---------- POST validation rejections ----------

    def test_post_out_of_range_rejected(self):
        resp = self.client.post(
            self._url(),
            data=_valid_payload(entry_point_x=1.5),
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("entry_point_x", resp.data)

    def test_post_non_axis_aligned_polygon_rejected(self):
        bad = _valid_payload(park_polygon=[
            [0.42, 0.55], [0.78, 0.55], [0.70, 0.92], [0.42, 0.92],
        ])
        resp = self.client.post(self._url(), data=bad, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("park_polygon", resp.data)

    def test_post_entry_inside_park_rejected(self):
        bad = _valid_payload(entry_point_x=0.55, entry_point_y=0.70)
        resp = self.client.post(self._url(), data=bad, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_post_unknown_camera_returns_404(self):
        resp = self.client.post(
            self._url(slug="does-not-exist"),
            data=_valid_payload(),
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    # ---------- VehicleAI gating ----------

    def test_post_when_vehicle_ai_disabled_returns_400(self):
        self.setting.license_vehicle_recognition = False
        self.setting.save()
        resp = self.client.post(self._url(), data=_valid_payload(), format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("error", resp.data)

    def test_post_when_target_camera_differs_returns_400(self):
        # license_vehicle_recognition=True but vehicle_recognition_camera points elsewhere.
        other = Camera.objects.create(
            name="Other", slug_name="other-camera-002", ip="192.168.1.100",
        )
        self.setting.vehicle_recognition_camera = other
        self.setting.save()
        resp = self.client.post(self._url(), data=_valid_payload(), format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    # ---------- DELETE ----------

    def test_delete_clears_all_four_fields(self):
        self.client.post(self._url(), data=_valid_payload(), format="json")
        resp = self.client.delete(self._url())
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.camera.refresh_from_db()
        self.assertIsNone(self.camera.vehicle_entry_point_x)
        self.assertIsNone(self.camera.vehicle_entry_point_y)
        self.assertIsNone(self.camera.vehicle_approach_angle_deg)
        self.assertIsNone(self.camera.vehicle_park_polygon)

    def test_delete_when_already_unset_returns_204(self):
        # DELETE is idempotent — clearing nothing is still a clean clear.
        resp = self.client.delete(self._url())
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

    def test_delete_unknown_camera_returns_404(self):
        resp = self.client.delete(self._url(slug="does-not-exist"))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    # ---------- Method-not-allowed safety ----------

    def test_put_not_allowed(self):
        resp = self.client.put(self._url(), data=_valid_payload(), format="json")
        self.assertEqual(resp.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_patch_not_allowed(self):
        resp = self.client.patch(self._url(), data={"entry_point_x": 0.5}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    # ---------- Coexistence smoke ----------

    def test_existing_zone_endpoint_still_works(self):
        # Posting vehicle calibration must NOT break the unrelated zone endpoint.
        # We just exercise GET on /cameras/zone, which lists zones.
        # Don't touch zone storage here; just confirm the route still responds.
        resp = self.client.get("/api/cameras/zone")
        # Either 200 OK with empty list, or 401 if auth blocks it — both prove the
        # endpoint is reachable, what we want to disprove is a 500 or 404.
        self.assertIn(resp.status_code, (
            status.HTTP_200_OK,
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ))
