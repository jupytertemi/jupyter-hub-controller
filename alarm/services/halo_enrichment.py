"""Halo /api/status enrichment.

The webhook handler writes a minimal AlarmDevice row immediately, then a
Celery task calls this to fill in fw_version + name from the Halo's own
HTTP API. Bounded retry with exponential backoff via Celery; this module
is the inner per-attempt logic.

Per firmware contract (see memory/halo-onboard-firmware-contracts.md):

  * ``GET http://{halo_ip}/api/status`` returns device, firmware, and
    operational state. Does NOT return mac_address.
  * ``GET /api/device_info`` does NOT exist — don't try.
  * MAC suffix (last 6 hex chars) is embedded in the slug:
    ``jupyter-alarm-eaa324`` → MAC suffix ``eaa324``. We use that for HA
    discovery's ``connections`` array; without OUI prefix the ``connections``
    entry is best-effort only — HA's ``unique_id`` is the primary identifier.
"""
import logging
import re
from typing import Dict, Optional

import requests

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"jupyter-alarm-([0-9a-fA-F]{6})$")


def fetch_status(halo_ip: str, timeout: float = 2.0) -> Optional[Dict]:
    """Single-shot GET; returns parsed JSON or None on any failure."""
    if not halo_ip:
        return None
    try:
        r = requests.get(f"http://{halo_ip}/api/status", timeout=timeout)
        if r.status_code == 200:
            return r.json()
        logger.debug("halo_status_http_%s ip=%s", r.status_code, halo_ip)
    except requests.RequestException as exc:
        logger.debug("halo_status_failed ip=%s: %s", halo_ip, exc)
    return None


def derive_mac_from_slug(slug: str) -> str:
    """Slug ``jupyter-alarm-eaa324`` → ``ea:a3:24`` (suffix only, no OUI).

    HA Auto-Discovery's ``connections`` array can take a partial MAC; we
    return the firmware-known suffix. Better than empty for HA dedup.
    """
    m = _SLUG_RE.match(slug)
    if not m:
        return ""
    raw = m.group(1).lower()
    return f"{raw[0:2]}:{raw[2:4]}:{raw[4:6]}"


def _type_from_serial(serial: str) -> Optional[str]:
    """Halo serials are baked at manufacture: JUP-OUTDR-XXXXXX (outdoor unit)
    or JUP-INDR-XXXXXX (indoor unit). Returns the corresponding AlarmType
    value, or None if the serial doesn't match either pattern (don't override
    the row's existing type in that case)."""
    if not serial:
        return None
    s = serial.upper()
    # Order matters — match the more specific token first
    if "OUTDR" in s:
        return "OUTDOOR"
    if "INDR" in s:
        return "INDOOR"
    return None


def merge_enrichment(slug: str, status: Optional[Dict]) -> Dict:
    """Build the dict of fields to apply to AlarmDevice from a /api/status
    response. Fields we DON'T trust the firmware to populate (mac_address)
    are derived from the slug.
    """
    out = {"mac_address": derive_mac_from_slug(slug)}
    if status:
        if status.get("firmware"):
            out["version_fw"] = status["firmware"]
        if status.get("device"):
            out["status_device_field"] = status["device"]
        # Build 156 item 7.5: Halo's serial encodes form factor.
        # Webhook initially defaults to INDOOR; this lets enrichment promote
        # to OUTDOOR (or confirm INDOOR) based on firmware-baked truth.
        type_from_serial = _type_from_serial(status.get("serial_number") or "")
        if type_from_serial:
            out["type_from_serial"] = type_from_serial
    return out
