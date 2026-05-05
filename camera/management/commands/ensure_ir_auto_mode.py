"""Set IR cut filter to AUTO on already-onboarded cameras so night plate OCR
works without any user action.

New cameras get this automatically at onboard via RTSPCameraManager (line ~422).
This command applies the same fix retroactively to cameras onboarded before
the auto-fix landed. Idempotent — re-running is safe.

Run on hub:
    python manage.py ensure_ir_auto_mode
    python manage.py ensure_ir_auto_mode --slug front-door
    python manage.py ensure_ir_auto_mode --dry-run
"""
import logging
import re
from urllib.parse import unquote

from django.core.management.base import BaseCommand
from onvif import ONVIFCamera, ONVIFError


def _extract_creds_from_rtsp(rtsp_url):
    """Pull username/password out of rtsp://user:pass@host/... — Camera rows
    onboarded before the username/password columns were populated keep them
    embedded in the URL only."""
    if not rtsp_url:
        return None, None
    m = re.match(r"rtsp://([^:]+):([^@]+)@", rtsp_url)
    if not m:
        return None, None
    return unquote(m.group(1)), unquote(m.group(2))

from camera.enums import CameraType
from camera.managers import RTSPCameraManager
from camera.models import Camera


class Command(BaseCommand):
    help = "Flip ONVIF IR cut filter to AUTO on existing cameras"

    def add_arguments(self, parser):
        parser.add_argument("--slug", help="Only this camera slug_name")
        parser.add_argument("--dry-run", action="store_true",
                            help="Print what would change without writing")

    def handle(self, *args, **opts):
        # Skip Ring cameras — they're not addressable via ONVIF.
        qs = Camera.objects.filter(is_enabled=True).exclude(type=CameraType.RING)
        if opts["slug"]:
            qs = qs.filter(slug_name=opts["slug"])

        manager = RTSPCameraManager()

        # Reuse the onboard helper. We just need to give it a connected ONVIFCamera
        # and the profiles list — the same shape as onvif_setup_camera.
        for cam in qs:
            self.stdout.write(f"--- {cam.slug_name} ({cam.name}) {cam.ip} ---")
            user = cam.username or ""
            pw = cam.password or ""
            if not user or not pw:
                user, pw = _extract_creds_from_rtsp(cam.rtsp_url)
            if not cam.ip or not user or not pw:
                self.stdout.write("  skip (no ip/creds)")
                continue
            try:
                # Try common ONVIF ports — same probe logic as onvif_setup_camera
                cam_fc = None
                for port in (80, 8080, 8000, 2020):
                    try:
                        cam_fc = ONVIFCamera(cam.ip, port, user, pw)
                        cam_fc.create_devicemgmt_service()
                        break
                    except Exception:
                        cam_fc = None
                if cam_fc is None:
                    self.stdout.write(self.style.WARNING("  ONVIF connect failed on all ports"))
                    continue
                cam_fc.create_media_service()
                profiles = cam_fc.media.GetProfiles()
                if opts["dry_run"]:
                    # Just inspect, don't write
                    try:
                        imaging = cam_fc.create_imaging_service()
                        token = profiles[0].VideoSourceConfiguration.SourceToken
                        current = imaging.GetImagingSettings({"VideoSourceToken": token})
                        ir = getattr(current, "IrCutFilter", "(unsupported)")
                        self.stdout.write(f"  IR mode: {ir}")
                    except Exception as e:
                        self.stdout.write(f"  imaging probe failed: {e}")
                    continue
                manager._ensure_ir_auto(cam_fc, profiles, cam.ip)
            except ONVIFError as e:
                self.stdout.write(self.style.WARNING(f"  ONVIF error: {e}"))
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"  failed: {e}"))
