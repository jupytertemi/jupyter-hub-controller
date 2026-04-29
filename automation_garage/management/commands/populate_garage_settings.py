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
        parser.add_argument(
            "--camera-id",
            type=int,
            default=None,
            help="Explicit Camera.id to bind. Required if multiple Camera rows exist.",
        )
        parser.add_argument(
            "--garage-id",
            type=int,
            default=None,
            help="Explicit MerossDevice.id to bind. Required if multiple MerossDevice rows exist.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print intended bindings + would-be settings, take no action.",
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

        # Resolve garage (Meross device)
        if opts["garage_id"] is not None:
            try:
                garage = MerossDevice.objects.get(id=opts["garage_id"])
            except MerossDevice.DoesNotExist:
                self.stderr.write(self.style.ERROR(
                    f"MerossDevice id={opts['garage_id']} not found."
                ))
                return
        else:
            meross_devices = list(MerossDevice.objects.all())
            if len(meross_devices) != 1:
                self.stdout.write(self.style.WARNING(
                    f"Found {len(meross_devices)} MerossDevice(s); pass --garage-id "
                    "explicitly or configure via app UI for multi-device hubs."
                ))
                for d in meross_devices:
                    self.stdout.write(f"  candidate: id={d.id} name={d.name!r}")
                return
            garage = meross_devices[0]

        # Resolve camera
        if opts["camera_id"] is not None:
            try:
                camera = Camera.objects.get(id=opts["camera_id"])
            except Camera.DoesNotExist:
                self.stderr.write(self.style.ERROR(
                    f"Camera id={opts['camera_id']} not found."
                ))
                return
        else:
            cameras = list(Camera.objects.all())
            if len(cameras) != 1:
                self.stdout.write(self.style.WARNING(
                    f"Found {len(cameras)} Camera(s); pass --camera-id explicitly "
                    "or configure via app UI for multi-camera hubs."
                ))
                for c in cameras:
                    self.stdout.write(f"  candidate: id={c.id} name={c.name!r}")
                return
            camera = cameras[0]

        if opts["dry_run"]:
            self.stdout.write(self.style.SUCCESS("DRY RUN — no changes made."))
            self.stdout.write(f"Would bind: garage='{garage.name}' (id={garage.id}), "
                              f"camera='{camera.name}' (id={camera.id})")
            self.stdout.write(f"Settings: active_open=True, auto_close=True, "
                              f"auto_close_delay={opts['auto_close_delay']}min, "
                              f"auto_open_on_owner=True, card_on_owner=True, "
                              f"card_on_unknown=False")
            self.stdout.write("To execute, drop --dry-run.")
            return

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
