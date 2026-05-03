import ipaddress
import re

from django.db import migrations


def _extract_ip_from_rtsp_url(url):
    if not url:
        return None
    s = url.split("://", 1)[1] if "://" in url else url
    if "@" in s:
        s = s.rsplit("@", 1)[1]
    host = re.split(r"[:/]", s, 1)[0]
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        return None


def backfill_ip(apps, schema_editor):
    """Idempotent: for each Camera with ip IS NULL but a parseable
    rtsp_url, populate ip. Heals existing fleet hubs whose RTSP cameras
    were created before extract_ip_from_rtsp_url landed in the
    serializer create() path. Also resets consecutive_failures so the
    health watchdog gives them a fresh window.
    """
    Camera = apps.get_model("camera", "Camera")
    qs = Camera.objects.filter(ip__isnull=True).exclude(rtsp_url__isnull=True).exclude(rtsp_url="")
    for cam in qs:
        ip = _extract_ip_from_rtsp_url(cam.rtsp_url)
        if ip:
            cam.ip = ip
            cam.consecutive_failures = 0
            cam.save(update_fields=["ip", "consecutive_failures"])


def noop_reverse(apps, schema_editor):
    """No-op reverse — backfilling ip is harmless to leave on rollback."""
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("camera", "0023_vehicle_detection_zone_and_m2m"),
    ]

    operations = [
        migrations.RunPython(backfill_ip, noop_reverse),
    ]
