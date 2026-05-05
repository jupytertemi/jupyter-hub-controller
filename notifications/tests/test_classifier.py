"""Unit tests for live_activity_publisher.classify_ai_event() — specifically
the CAR-branch widening from BACKEND_OPEN_PRS_BUNDLE.md §A.

Pre-fix: passing/spotted cars and unknown-owner cars silently dropped at
the classifier (returned None). Post-fix: every CAR event fires either
"garage_detected" (known owner + Approaching/Parked/Departing → drives
garage automation) or "vehicle_spotted" (everything else → banner only).

Run on hub via:
    DJANGO_SETTINGS_MODULE=hub_controller.settings.production \
        python manage.py test notifications.tests.test_classifier
"""
from django.test import SimpleTestCase

# live_activity_publisher.py sits at the project root next to manage.py;
# importable directly. The module loads .env at import time and reads
# APNS_* vars, so it must be imported INSIDE the test environment where
# those vars are present (i.e., on a real hub or a test runner with the
# env preloaded). See module docstring above for the run command.
from live_activity_publisher import classify_ai_event


class CarClassifierTests(SimpleTestCase):
    """Per BACKEND_OPEN_PRS_BUNDLE.md §A — receipts that the CAR branch
    fires notifications for every state x owner combination, with the
    known-owner driveway path preserved unchanged (regression guard)."""

    def test_car_spotted_unknown_owner_fires_generic_banner(self):
        nt, t, b = classify_ai_event({
            "label": "CAR",
            "vehicle_status": "Spotted",
            "camera_name": "Front Door",
        })
        self.assertEqual(nt, "vehicle_spotted")
        self.assertEqual(t, "Vehicle spotted")
        self.assertIn("Front Door", b)

    def test_car_spotted_known_owner_fires_named_banner(self):
        nt, t, b = classify_ai_event({
            "label": "CAR",
            "vehicle_status": "Spotted",
            "recognized_name": "Temi",
            "camera_name": "Front Door",
        })
        self.assertEqual(nt, "vehicle_spotted")
        self.assertIn("Temi", t)
        self.assertIn("Temi", b)

    def test_car_approaching_unknown_owner_now_fires(self):
        """Pre-fix this returned None and dropped the event."""
        nt, _, _ = classify_ai_event({
            "label": "CAR",
            "vehicle_status": "Approaching",
            "camera_name": "Front Door",
        })
        self.assertEqual(nt, "vehicle_spotted")

    def test_car_approaching_known_owner_unchanged(self):
        """Existing driveway / garage-automation path must keep working —
        regression guard. notification_type stays "garage_detected" so
        downstream garage routing is preserved."""
        nt, t, _ = classify_ai_event({
            "label": "CAR",
            "vehicle_status": "Approaching",
            "recognized_name": "Temi",
            "camera_name": "Front Door",
        })
        self.assertEqual(nt, "garage_detected")
        self.assertIn("Temi", t)
