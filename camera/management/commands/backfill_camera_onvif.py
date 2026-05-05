"""One-shot backfill for cameras with empty onvif_manufacturer / onvif_model.

Walks every Camera row, fills the gaps:

  • Ring cameras → set onvif_manufacturer="Ring", onvif_model="Ring Camera"
    (Ring doesn't speak ONVIF; cloud-only via ring_mqtt. Future improvement:
    swap in the precise product name from ring_mqtt's <id>/info MQTT topic.)

  • RTSP cameras with non-empty IP+credentials → call get_onvif_device_info()
    and persist whatever ONVIF returns. Skips RTSP cameras with empty creds
    since the probe always 401s on those — operator has to enter creds first.

Idempotent. Safe to run repeatedly. Useful after deploying the inline+celery
probe changes when the existing fleet has a backlog of empty rows.

Usage:
    python manage.py backfill_camera_onvif
    python manage.py backfill_camera_onvif --dry-run
    python manage.py backfill_camera_onvif --slug=front-door-ad39d9
"""

from django.core.management.base import BaseCommand

from camera.enums import CameraType
from camera.models import Camera, RTSPCamera


class Command(BaseCommand):
    help = "Backfill onvif_manufacturer / onvif_model on existing camera rows"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would change without writing to the DB.",
        )
        parser.add_argument(
            "--slug",
            type=str,
            default=None,
            help="Only process the camera with this slug_name.",
        )

    def handle(self, *args, **opts):
        dry_run = opts["dry_run"]
        slug = opts["slug"]

        qs = Camera.objects.all()
        if slug:
            qs = qs.filter(slug_name=slug)

        ring_count = 0
        rtsp_probed = 0
        rtsp_skipped_no_creds = 0
        rtsp_failed = 0
        unchanged = 0

        for cam in qs:
            mfr_empty = not (cam.onvif_manufacturer or "").strip()
            mdl_empty = not (cam.onvif_model or "").strip()

            if not mfr_empty and not mdl_empty:
                unchanged += 1
                continue

            # Ring cameras — set defaults, no probe
            if cam.type == CameraType.RING:
                new_mfr = cam.onvif_manufacturer or "Ring"
                new_mdl = cam.onvif_model or "Ring Camera"
                self.stdout.write(
                    f"[ring]  {cam.slug_name}: mfr={new_mfr!r} model={new_mdl!r}"
                    + (" (dry-run)" if dry_run else "")
                )
                if not dry_run:
                    cam.onvif_manufacturer = new_mfr
                    cam.onvif_model = new_mdl
                    cam.save(update_fields=[
                        "onvif_manufacturer", "onvif_model", "updated_at",
                    ])
                ring_count += 1
                continue

            # RTSP cameras — need IP + creds to probe
            if cam.type == CameraType.RTSP:
                if not cam.ip:
                    self.stdout.write(
                        self.style.WARNING(
                            f"[skip] {cam.slug_name}: RTSP but no IP recorded"
                        )
                    )
                    rtsp_skipped_no_creds += 1
                    continue
                if not (cam.username or "").strip() or not (cam.password or "").strip():
                    self.stdout.write(
                        self.style.WARNING(
                            f"[skip] {cam.slug_name}: RTSP but empty creds — "
                            f"operator must save username+password before ONVIF "
                            f"probe will succeed"
                        )
                    )
                    rtsp_skipped_no_creds += 1
                    continue

                self.stdout.write(
                    f"[rtsp] {cam.slug_name}: probing {cam.ip} ..."
                )
                result = RTSPCamera.objects.get_onvif_device_info(
                    cam.ip,
                    username=cam.username or "",
                    password=cam.password or "",
                )
                if not result or not (
                    result.get("manufacturer") or result.get("model")
                ):
                    self.stdout.write(
                        self.style.ERROR(
                            f"       FAILED — camera unreachable, ONVIF disabled, "
                            f"or creds wrong"
                        )
                    )
                    rtsp_failed += 1
                    continue

                new_mfr = result.get("manufacturer", "")
                new_mdl = result.get("model", "")
                self.stdout.write(
                    self.style.SUCCESS(
                        f"       → mfr={new_mfr!r} model={new_mdl!r}"
                        + (" (dry-run)" if dry_run else "")
                    )
                )
                if not dry_run:
                    cam.onvif_manufacturer = new_mfr
                    cam.onvif_model = new_mdl
                    cam.save(update_fields=[
                        "onvif_manufacturer", "onvif_model", "updated_at",
                    ])
                rtsp_probed += 1

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Done. ring={ring_count} rtsp_probed={rtsp_probed} "
                f"rtsp_skipped_no_creds={rtsp_skipped_no_creds} "
                f"rtsp_failed={rtsp_failed} unchanged={unchanged}"
                + (" (DRY RUN — no DB writes)" if dry_run else "")
            )
        )
