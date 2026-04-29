"""
Idempotent seed for GarageDoorSettings.

Runs at gold-image entrypoint (entrypoint_migrate.sh, after migrations) so that
hubs flashed from the gold image come online with garage automation already
wired *iff* exactly one MerossDevice + one Camera exist. Multi-device hubs are
left alone — the user wires those via the app UI as before.

Hardcoded values would break the gold-image transferable model — every value
read here comes from DB rows the cloud/app has already provisioned.
"""

from django.core.management.base import BaseCommand
from rest_framework.exceptions import ValidationError

from automation_garage.models import GarageDoorSettings
from camera.models import Camera
from meross.models import MerossDevice


class Command(BaseCommand):
    help = (
        "Seed a default GarageDoorSettings row when exactly one MerossDevice "
        "and one Camera exist, no row exists yet, and HA can introspect the "
        "device's cover entity. Idempotent."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-create the row even if one exists (DELETES + recreates HA automations).",
        )
        parser.add_argument(
            "--auto-close-delay",
            type=int,
            default=5,
            help="Minutes after open before HA's belt-and-braces auto-close fires (default: 5).",
        )

    def handle(self, *args, **opts):
        existing = GarageDoorSettings.objects.first()
        if existing and not opts["force"]:
            self.stdout.write(
                self.style.WARNING(
                    f"GarageDoorSettings already exists (id={existing.id}); "
                    "skipping. Use --force to re-create."
                )
            )
            return

        meross_devices = list(MerossDevice.objects.all())
        cameras = list(Camera.objects.all())

        if len(meross_devices) != 1:
            self.stdout.write(
                self.style.WARNING(
                    f"Found {len(meross_devices)} MerossDevice(s); seed only runs "
                    "when exactly one exists. Configure via app UI for multi-device hubs."
                )
            )
            return
        if len(cameras) != 1:
            self.stdout.write(
                self.style.WARNING(
                    f"Found {len(cameras)} Camera(s); seed only runs when exactly one "
                    "exists. Configure via app UI for multi-camera hubs."
                )
            )
            return

        garage = meross_devices[0]
        camera = cameras[0]

        if existing:
            self.stdout.write(f"--force: deleting existing GarageDoorSettings id={existing.id}")
            try:
                GarageDoorSettings.objects.delete_instance(existing)
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"Failed to delete existing row: {e}"))
                # Continue — we'll create the new row anyway.

        self.stdout.write(
            f"Seeding GarageDoorSettings: garage='{garage.name}' (id={garage.id}), "
            f"camera='{camera.name}' (id={camera.id})"
        )
        try:
            row = GarageDoorSettings.objects.create(
                garage=garage,
                camera=camera,
                active_open=True,
                auto_close=True,
                auto_close_delay=opts["auto_close_delay"],
                auto_open_on_owner=True,
                card_on_owner=True,
                card_on_unknown=False,
            )
        except ValidationError as e:
            self.stderr.write(self.style.ERROR(
                f"HA cover introspection failed (likely cover sensor not yet exposed): {e.detail}. "
                "Re-run after Meross device finishes its first sync."
            ))
            return
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Seed failed: {e}"))
            return

        self.stdout.write(self.style.SUCCESS(
            f"GarageDoorSettings seeded (id={row.id}); HA automations created."
        ))
