"""Tests for vehicle_detection_zone propagation into Frigate config.

Verifies that when a Camera has vehicle_detection_zone populated, the
get_cameras() builder injects a zones entry that the Frigate template will
render. CW#172 — all Frigate config flows through camera/templates/*.

Run via:
    DJANGO_SETTINGS_MODULE=hub_controller.settings.local \
        python manage.py test camera.tests.test_vehicle_zone_frigate_render
"""
from django.test import TestCase

from camera.models import Camera
from camera.tasks import get_cameras


class VehicleDetectionZoneFrigateBuilderTests(TestCase):

    def setUp(self):
        self.camera = Camera.objects.create(
            name="Front Driveway",
            slug_name="front-driveway",
            ip="192.168.1.50",
            type="RTSP",
            rtsp_url="rtsp://test/main",
        )

    def _valid_zone(self):
        return [[0.10, 0.10], [0.90, 0.10], [0.90, 0.90], [0.10, 0.90]]

    def test_zone_unset_no_zone_entry(self):
        cams = [c for c in get_cameras() if c["name"] == "front-driveway"]
        self.assertEqual(len(cams), 1)
        names = [z["name"] for z in cams[0]["zones"]]
        self.assertNotIn("vehicle_detection_zone", names)

    def test_zone_set_zone_entry_published(self):
        self.camera.vehicle_detection_zone = self._valid_zone()
        self.camera.save(update_fields=["vehicle_detection_zone"])
        cams = [c for c in get_cameras() if c["name"] == "front-driveway"]
        zones = cams[0]["zones"]
        match = [z for z in zones if z["name"] == "vehicle_detection_zone"]
        self.assertEqual(len(match), 1)
        # Objects scoped to vehicles only.
        self.assertEqual(set(match[0]["objects"]), {"car", "truck", "motorcycle", "bus"})

    def test_zone_pixel_conversion_rtsp_1920x1080(self):
        # RTSP cameras use 1920x1080 in _zone_coords_to_pixels.
        self.camera.type = "RTSP"
        self.camera.vehicle_detection_zone = [[0.10, 0.10], [0.90, 0.10], [0.90, 0.90], [0.10, 0.90]]
        self.camera.save()
        cams = [c for c in get_cameras() if c["name"] == "front-driveway"]
        zones = [z for z in cams[0]["zones"] if z["name"] == "vehicle_detection_zone"]
        # 0.10*1920=192, 0.10*1080=108, 0.90*1920=1728, 0.90*1080=972
        self.assertEqual(zones[0]["coordinates"], "192,108,1728,108,1728,972,192,972")

    def test_zone_pixel_conversion_ring_720x720(self):
        # Ring cameras use 720x720 (square).
        self.camera.type = "RING"
        self.camera.vehicle_detection_zone = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]
        self.camera.save()
        cams = [c for c in get_cameras() if c["name"] == "front-driveway"]
        zones = [z for z in cams[0]["zones"] if z["name"] == "vehicle_detection_zone"]
        self.assertEqual(zones[0]["coordinates"], "0,0,720,0,720,720,0,720")

    def test_multiple_cameras_independent_zones(self):
        cam2 = Camera.objects.create(
            name="Garage",
            slug_name="garage",
            ip="192.168.1.51",
            type="RTSP",
            rtsp_url="rtsp://test/garage",
            vehicle_detection_zone=[[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]],
        )
        self.camera.vehicle_detection_zone = self._valid_zone()
        self.camera.save()

        cams = {c["name"]: c for c in get_cameras()}
        self.assertIn("front-driveway", cams)
        self.assertIn("garage", cams)

        front_zones = [z for z in cams["front-driveway"]["zones"] if z["name"] == "vehicle_detection_zone"]
        garage_zones = [z for z in cams["garage"]["zones"] if z["name"] == "vehicle_detection_zone"]
        # Different polygons → different rendered coordinates.
        self.assertNotEqual(front_zones[0]["coordinates"], garage_zones[0]["coordinates"])
