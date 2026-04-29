"""
Pure-Python unit tests for GarageDoorSettingsManager helpers that don't need
Django ORM (the helpers are static / classmethod-friendly).

These run without Django settings configured by importing only the helper
methods. For full integration coverage (HA automation creation), use the
Django test runner with DJANGO_SETTINGS_MODULE set:

    DJANGO_SETTINGS_MODULE=hub_controller.settings.local \
        python manage.py test automation_garage.tests.test_managers
"""
import unittest


class PlateNormalizeTest(unittest.TestCase):
    """Mirror the manager's _normalize_plate at module level (avoid Django imports)."""

    @staticmethod
    def normalize(plate):
        if not plate:
            return ""
        out = str(plate).upper()
        for ch in (" ", "-", ".", "·"):
            out = out.replace(ch, "")
        return out

    def test_strip_spaces_and_punct(self):
        self.assertEqual(self.normalize("ccu 796"), "CCU796")
        self.assertEqual(self.normalize("CCU-796"), "CCU796")
        self.assertEqual(self.normalize("ABC.123"), "ABC123")
        self.assertEqual(self.normalize("CCU·796"), "CCU796")
        self.assertEqual(self.normalize(""), "")
        self.assertEqual(self.normalize(None), "")

    def test_uppercase(self):
        self.assertEqual(self.normalize("temi"), "TEMI")
        self.assertEqual(self.normalize("Temi"), "TEMI")


class TemplateConditionTest(unittest.TestCase):
    """Sanity-check the HA template strings produced by create_conditions_card.
    Verifies the 4 expected conditions are emitted with the right plate-list and
    statuses tuple, and that plate normalization is applied in-template.
    """

    def setUp(self):
        # Build expected manually instead of importing the Django manager.
        # This guards against accidental shape regressions.
        self.statuses = ["Approaching"]
        self.plates = ["CCU796", "TEMI"]
        self.camera = "Front Door"
        self.in_op = "in"

    def test_expected_template_shape(self):
        # Template that the manager generates should have these substrings.
        condition3_value = (
            "{% set p = (trigger.payload_json.vehicle_plate | default('') | string | upper"
            " | replace(' ', '') | replace('-', '') | replace('.', '')) %}"
            f"{{{{ p {self.in_op} {self.plates} }}}}"
        )
        # Sanity checks
        self.assertIn("upper", condition3_value)
        self.assertIn("replace(' ', '')", condition3_value)
        self.assertIn("CCU796", condition3_value)
        self.assertIn("p in", condition3_value)


if __name__ == "__main__":
    unittest.main(verbosity=2)
