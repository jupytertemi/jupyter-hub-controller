"""Per-Halo API token derivation.

Halos in the field today authenticate firmware-version reports with
``FRV_API_KEY`` — a fleet-shared static key. If any Halo's NVS is exfiltrated
(physical tear-down) the attacker has hub-wide access for every customer.

v1.6 onboard mints a per-Halo token by HMAC-SHA256-deriving from
``HUB_SECRET`` + the Halo's slug. Properties:

  * Stateless on the hub (no DB lookup; we re-derive to validate).
  * Compromise of one Halo's NVS only burns that one Halo's token.
  * Rotates with ``HUB_SECRET`` rotation (nuclear option for full fleet).
  * Differs across hubs even for the same slug, so a Halo bonded to
    one hub doesn't accidentally authenticate against another.

Existing onboarded Halos keep using ``FRV_API_KEY`` until they're
re-onboarded. The verify path accepts BOTH for the deprecation window.
"""
import hmac
import hashlib
import os

from django.conf import settings


def _hub_secret() -> str:
    """HUB_SECRET is in /root/jupyter-hub-controller/.env (sourced into the
    process environment by start_hub_controller.sh). Not loaded into Django
    `settings` directly. Read from `os.environ` defensively.
    """
    val = os.environ.get("HUB_SECRET", "")
    if not val:
        # Fallback: try to read from .env directly (covers Celery worker
        # contexts where env may have been re-loaded incompletely)
        try:
            with open("/root/jupyter-hub-controller/.env", "r") as f:
                for line in f:
                    if line.startswith("HUB_SECRET="):
                        val = line.split("=", 1)[1].strip()
                        break
        except OSError:
            pass
    return val


def derive_halo_api_token(slug: str) -> str:
    """Per-Halo HMAC token. Deterministic — recompute to validate."""
    if not slug:
        raise ValueError("slug is required")
    secret = _hub_secret()
    if not secret:
        raise RuntimeError("HUB_SECRET unavailable — cannot derive Halo API token")
    return hmac.new(
        key=secret.encode("utf-8"),
        msg=f"halo:{slug}".encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()


def verify_halo_api_token(slug: str, token: str) -> bool:
    """Constant-time comparison so timing oracles can't enumerate tokens."""
    if not slug or not token:
        return False
    try:
        expected = derive_halo_api_token(slug)
    except RuntimeError:
        return False
    return hmac.compare_digest(expected, token)


def verify_legacy_or_modern_key(slug: str, presented_key: str) -> bool:
    """Accept either legacy fleet-shared FRV_API_KEY OR per-Halo HMAC token.

    Used by ``UpdateAlarmDeviceVersionFW`` during the deprecation window.
    Existing Halos in the field send ``FRV_API_KEY``; new onboards (v1.6+)
    send the per-Halo token.
    """
    if not presented_key:
        return False
    # Legacy path — fleet-shared
    frv = getattr(settings, "FRV_API_KEY", "")
    if frv and hmac.compare_digest(presented_key, frv):
        return True
    # Modern path — per-Halo HMAC
    return verify_halo_api_token(slug, presented_key)
