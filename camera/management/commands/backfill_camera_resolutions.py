"""Backfill main_stream_width/height + sub_stream_width/height for existing cameras.

Reuses the same Tier1/Tier2 probe helper as the onboard path — ffprobe RTSP
first, JPEG thumbnail header as fallback. Idempotent; only updates fields
currently null (or all with --force).

Run on hub:
    python manage.py backfill_camera_resolutions
    python manage.py backfill_camera_resolutions --slug front-door --force
    python manage.py backfill_camera_resolutions --dry-run
"""
import os

from django.core.management.base import BaseCommand

from camera.managers import RTSPCameraManager, THUMBNAILS_DIR
from camera.models import Camera


class Command(BaseCommand):
    help = "Probe + backfill stream resolutions for existing cameras"

    def add_arguments(self, parser):
        parser.add_argument("--slug", help="Probe only this camera slug_name")
        parser.add_argument("--force", action="store_true",
                            help="Re-probe even cameras with resolutions already set")
        parser.add_argument("--dry-run", action="store_true",
                            help="Print what would change without saving")

    def handle(self, *args, **opts):
        qs = Camera.objects.exclude(rtsp_url__isnull=True).exclude(rtsp_url="")
        if opts["slug"]:
            qs = qs.filter(slug_name=opts["slug"])
        if not opts["force"]:
            qs = qs.filter(main_stream_width__isnull=True) | qs.filter(
                sub_stream_width__isnull=True, sub_rtsp_url__isnull=False
            )

        manager = RTSPCameraManager()
        total = qs.count()
        self.stdout.write(f"{total} camera(s) to probe (dry_run={opts['dry_run']})")

        for cam in qs.distinct():
            self.stdout.write(f"--- {cam.slug_name} ({cam.name}) ---")
            updates = {}
            thumb_path = os.path.join(THUMBNAILS_DIR, f"{cam.slug_name}.jpg")
            mw, mh = manager._probe_stream_resolution(
                cam.rtsp_url, fallback_image_path=thumb_path
            )
            if mw and mh and (opts["force"] or not cam.main_stream_width):
                updates["main_stream_width"] = mw
                updates["main_stream_height"] = mh
                self.stdout.write(f"  main: {mw}x{mh}")
            elif not (mw and mh):
                self.stdout.write(self.style.WARNING(
                    "  main: probe failed (no thumbnail and ffprobe error)"))
            if cam.sub_rtsp_url and cam.sub_rtsp_url != cam.rtsp_url:
                sw, sh = manager._probe_stream_resolution(cam.sub_rtsp_url)
                if sw and sh and (opts["force"] or not cam.sub_stream_width):
                    updates["sub_stream_width"] = sw
                    updates["sub_stream_height"] = sh
                    self.stdout.write(f"  sub:  {sw}x{sh}")
            if not updates:
                self.stdout.write("  no changes")
                continue
            if not opts["dry_run"]:
                for k, v in updates.items():
                    setattr(cam, k, v)
                cam.save(update_fields=list(updates.keys()))
                self.stdout.write(self.style.SUCCESS("  saved"))
