"""Hub WiFi frequency check — deterministic alternative to SSID-name heuristic.

Many home routers use band-steering with the same SSID for both 2.4 GHz and
5 GHz. The hub may end up on EITHER band, but the Halo (ESP32-S3, 2.4 GHz
only) just needs 2.4 GHz to be AVAILABLE on the same SSID — both devices
end up on the same logical network regardless of which band the hub is on.

So the right check isn't "is hub on 2.4 GHz?" — it's "is the hub's SSID
broadcasting a 2.4 GHz BSSID anywhere visible?" If yes, Halo can join. If
no (rare modern WiFi 6E ax-only setups), real failure with an actionable
error.
"""
import logging
import re
import shutil
import subprocess
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# 5 GHz band: 5180-5825 MHz typical; 2.4 GHz: 2412-2484 MHz
_FREQ_5GHZ_LOWER = 5000


def _wifi_iface() -> Optional[str]:
    """Find the active WiFi iface by walking /sys/class/net."""
    try:
        out = subprocess.check_output(["iw", "dev"], text=True, timeout=2)
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("Interface"):
                return line.split()[1]
    except Exception as exc:
        logger.warning("iw_dev_iface_failed: %s", exc)
    return None


def hub_wifi_status() -> Tuple[Optional[str], Optional[int]]:
    """Returns (ssid, freq_mhz) for the hub's current WiFi association,
    or (None, None) if not connected to WiFi or ``iw`` isn't available.
    """
    if not shutil.which("iw"):
        logger.warning("iw_binary_not_found — cannot determine WiFi frequency")
        return None, None

    iface = _wifi_iface()
    if not iface:
        return None, None

    try:
        out = subprocess.check_output(
            ["iw", "dev", iface, "link"], text=True, timeout=2,
        )
    except subprocess.CalledProcessError as exc:
        logger.warning("iw_link_failed iface=%s: %s", iface, exc)
        return None, None
    except Exception as exc:
        logger.warning("iw_link_unexpected iface=%s: %s", iface, exc)
        return None, None

    if "Not connected" in out:
        return None, None

    ssid = None
    freq = None
    m = re.search(r"SSID:\s*(.+?)$", out, re.MULTILINE)
    if m:
        ssid = m.group(1).strip()
    m = re.search(r"freq:\s*(\d+)", out)
    if m:
        freq = int(m.group(1))
    return ssid, freq


def is_5ghz(freq_mhz: int) -> bool:
    return freq_mhz >= _FREQ_5GHZ_LOWER


def ssid_has_2_4ghz_band(ssid: str, force_rescan: bool = True) -> Tuple[bool, list]:
    """Returns (has_2_4ghz, scanned_freqs) for the given SSID.

    Looks at ALL visible APs broadcasting `ssid`, returns True if any are
    on a 2.4 GHz channel (< 5000 MHz). The hub's own current band doesn't
    matter — what matters is whether the same network has a 2.4 GHz half.

    `scanned_freqs` is the list of frequencies we saw for this SSID; useful
    for diagnostics (e.g. surfacing "we saw 5745 MHz only — no 2.4 GHz").

    Conservative default: if scan fails for any reason, return (True, [])
    so we don't false-reject onboarding due to a transient nmcli quirk.
    Better to let the Halo try and fail than to block users incorrectly.
    """
    if not ssid:
        return True, []

    if force_rescan:
        try:
            # Dispatch a rescan; nmcli returns when scan starts (not when done)
            subprocess.run(
                ["nmcli", "dev", "wifi", "rescan"],
                timeout=5, check=False,
                capture_output=True,
            )
            # Brief wait for results to populate
            import time as _t
            _t.sleep(2.5)
        except Exception:
            pass

    # Use --escape no so colons inside SSID are NOT escaped to \:
    # Output format: SSID:FREQ on each line. FREQ is in MHz.
    try:
        out = subprocess.check_output(
            ["nmcli", "--terse", "--escape", "no",
             "--fields", "SSID,FREQ",
             "device", "wifi", "list"],
            text=True, timeout=4,
        )
    except Exception as exc:
        logger.warning("nmcli_wifi_list_failed: %s — defaulting to has_2_4ghz=True", exc)
        return True, []  # conservative default

    found_freqs = []
    has_2_4 = False
    for line in out.strip().splitlines():
        # nmcli with --escape no still uses : as field separator. Last
        # field is always FREQ (numeric). Take rsplit so SSIDs containing
        # `:` end up in the SSID field.
        if ":" not in line:
            continue
        scanned_ssid, freq_str = line.rsplit(":", 1)
        freq_str = freq_str.strip()
        if not freq_str:
            continue
        try:
            # nmcli returns freq as just an integer (e.g. "2462")
            freq = int(freq_str)
        except ValueError:
            continue
        if scanned_ssid == ssid:
            found_freqs.append(freq)
            if freq < _FREQ_5GHZ_LOWER:
                has_2_4 = True

    if not found_freqs:
        # SSID not found in scan — could be hidden or scan stale. Don't
        # block onboard on this; default to True and let the actual
        # WiFi join surface any real problem.
        logger.info("ssid_not_in_scan ssid=%s — assuming 2.4 available", ssid)
        return True, []

    return has_2_4, sorted(set(found_freqs))
