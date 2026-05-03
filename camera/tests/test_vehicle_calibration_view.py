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

    # ---------- 2026-05-03: detection_zone field (additive) ----------

    def _valid_zone(self):
        return [[0.10, 0.10], [0.90, 0.10], [0.90, 0.90], [0.10, 0.90]]

    def test_post_with_detection_zone_persists_to_db(self):
        from unittest.mock import patch
        with patch("camera.views.update_frigate_config") as mock_render:
            mock_render.delay = lambda: None
            resp = self.client.post(
                self._url(),
                data=_valid_payload(detection_zone=self._valid_zone()),
                format="json",
            )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.camera.refresh_from_db()
        self.assertEqual(self.camera.vehicle_detection_zone, self._valid_zone())
        # Existing fields still populated alongside.
        self.assertEqual(self.camera.vehicle_entry_point_x, 0.18)

    def test_get_returns_detection_zone_when_set(self):
        self.camera.vehicle_detection_zone = self._valid_zone()
        self.camera.vehicle_entry_point_x = 0.18
        self.camera.vehicle_entry_point_y = 0.42
        self.camera.vehicle_approach_angle_deg = 295.0
        self.camera.vehicle_park_polygon = [[0.42, 0.55], [0.78, 0.55], [0.78, 0.92], [0.42, 0.92]]
        self.camera.save()
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["detection_zone"], self._valid_zone())

    def test_post_without_detection_zone_leaves_field_unchanged(self):
        # Pre-populate detection_zone, then POST without it. Should NOT zero it.
        self.camera.vehicle_detection_zone = self._valid_zone()
        self.camera.save(update_fields=["vehicle_detection_zone"])
        from unittest.mock import patch
        with patch("camera.views.update_frigate_config") as mock_render:
            mock_render.delay = lambda: None
            resp = self.client.post(self._url(), data=_valid_payload(), format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.camera.refresh_from_db()
        self.assertEqual(self.camera.vehicle_detection_zone, self._valid_zone())

    def test_post_with_detection_zone_triggers_frigate_render(self):
        from unittest.mock import patch
        with patch("camera.tasks.update_frigate_config.delay") as mock_delay:
            resp = self.client.post(
                self._url(),
                data=_valid_payload(detection_zone=self._valid_zone()),
                format="json",
            )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        mock_delay.assert_called_once()

    def test_post_unchanged_zone_does_not_trigger_render(self):
        self.camera.vehicle_detection_zone = self._valid_zone()
        self.camera.save(update_fields=["vehicle_detection_zone"])
        from unittest.mock import patch
        with patch("camera.tasks.update_frigate_config.delay") as mock_delay:
            resp = self.client.post(self._url(), data=_valid_payload(), format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        mock_delay.assert_not_called()

    def test_delete_with_zone_set_triggers_frigate_render(self):
        self.camera.vehicle_detection_zone = self._valid_zone()
        self.camera.vehicle_entry_point_x = 0.5
        self.camera.vehicle_entry_point_y = 0.5
        self.camera.vehicle_approach_angle_deg = 90.0
        self.camera.vehicle_park_polygon = [[0.4, 0.5], [0.6, 0.5], [0.6, 0.7], [0.4, 0.7]]
        self.camera.save()
        from unittest.mock import patch
        with patch("camera.tasks.update_frigate_config.delay") as mock_delay:
            resp = self.client.delete(self._url())
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        mock_delay.assert_called_once()
        self.camera.refresh_from_db()
        self.assertIsNone(self.camera.vehicle_detection_zone)

    def test_post_zone_with_3_points_rejected(self):
        bad = _valid_payload(detection_zone=[[0.1, 0.1], [0.9, 0.1], [0.5, 0.9]])
        resp = self.client.post(self._url(), data=bad, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_post_zone_out_of_range_rejected(self):
        bad = _valid_payload(detection_zone=[[1.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]])
        resp = self.client.post(self._url(), data=bad, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_post_authorized_via_m2m_membership(self):
        # Camera is NOT in vehicle_recognition_camera (legacy ForeignKey) but IS
        # in the new M2M. Should be authorized to write calibration.
        self.setting.vehicle_recognition_camera = None
        self.setting.save()
        self.setting.vehicle_recognition_cameras.add(self.camera)
        from unittest.mock import patch
        with patch("camera.views.update_frigate_config") as mock_render:
            mock_render.delay = lambda: None
            resp = self.client.post(
                self._url(),
                data=_valid_payload(detection_zone=self._valid_zone()),
                format="json",
            )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_post_unauthorized_when_not_in_either_path(self):
        self.setting.vehicle_recognition_camera = None
        self.setting.save()
        # M2M is empty.
        resp = self.client.post(
            self._url(),
            data=_valid_payload(detection_zone=self._valid_zone()),
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    # ---------- Delete cascade (2026-05-03) ----------

    def test_delete_removes_camera_from_legacy_fk(self):
        # Setting has this camera as the legacy single-select; DELETE on
        # vehicle-calibration must clear the FK.
        self.assertEqual(self.setting.vehicle_recognition_camera_id, self.camera.id)
        self.camera.vehicle_detection_zone = self._valid_zone()
        self.camera.save(update_fields=["vehicle_detection_zone"])
        from unittest.mock import patch
        with patch("camera.views.update_frigate_config") as mock_render:
            mock_render.delay = lambda: None
            resp = self.client.delete(self._url())
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.setting.refresh_from_db()
        self.assertIsNone(self.setting.vehicle_recognition_camera)
        # Last-camera cascade: no other cameras → license disabled.
        self.assertFalse(self.setting.license_vehicle_recognition)

    def test_delete_removes_camera_from_m2m(self):
        # Two cameras in M2M; delete one keeps the other and keeps license on.
        cam2 = Camera.objects.create(name="Cam2", slug_name="cam-002", ip="192.168.1.100")
        self.setting.vehicle_recognition_camera = None
        self.setting.save()
        self.setting.vehicle_recognition_cameras.add(self.camera, cam2)
        self.camera.vehicle_detection_zone = self._valid_zone()
        self.camera.save(update_fields=["vehicle_detection_zone"])
        from unittest.mock import patch
        with patch("camera.views.update_frigate_config") as mock_render:
            mock_render.delay = lambda: None
            resp = self.client.delete(self._url())
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.setting.refresh_from_db()
        # cam2 still in M2M, this camera removed.
        m2m_ids = list(self.setting.vehicle_recognition_cameras.values_list("id", flat=True))
        self.assertNotIn(self.camera.id, m2m_ids)
        self.assertIn(cam2.id, m2m_ids)
        # License stays ON because cam2 remains.
        self.assertTrue(self.setting.license_vehicle_recognition)

    def test_delete_last_camera_disables_feature(self):
        # Only this camera in M2M; delete it → feature should disable globally.
        self.setting.vehicle_recognition_camera = None
        self.setting.save()
        self.setting.vehicle_recognition_cameras.set([self.camera])
        self.camera.vehicle_detection_zone = self._valid_zone()
        self.camera.save(update_fields=["vehicle_detection_zone"])
        from unittest.mock import patch
        with patch("camera.views.update_frigate_config") as mock_render:
            mock_render.delay = lambda: None
            resp = self.client.delete(self._url())
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.setting.refresh_from_db()
        self.assertEqual(self.setting.vehicle_recognition_cameras.count(), 0)
        self.assertFalse(self.setting.license_vehicle_recognition)

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
