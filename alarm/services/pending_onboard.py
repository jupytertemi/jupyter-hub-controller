"""Pending-onboard registry — authorisation gate for the auto-create webhook.

Without this, any ESP that opens a TCP socket to the hub on port 4444 and
sends a register payload would have an ``AlarmDevice`` row created for it.
That's a hostile-LAN problem in retail / multi-tenant settings.

When the app calls ``GET /api/halo/onboard-payload?slug=X``, we mark that
slug pending in Redis with a 5-minute TTL. The transfer_server webhook
checks for the pending key before creating the row. Anything that isn't
in the pending set is rejected with 403, no DB changes.
"""
import logging

import redis
from django.conf import settings

logger = logging.getLogger(__name__)

# Reuse the Celery broker connection; same Redis instance.
_redis_url = getattr(settings, "CELERY_BROKER_URL", "redis://localhost:6379/0")
_redis_client = redis.from_url(_redis_url, decode_responses=True)

PENDING_TTL_SEC = 300  # 5 minutes from app fetching the bonding payload
ONBOARD_STARTED_TTL_SEC = 1800  # 30 minutes — outlives the pending key so
                                # wait-online can still report timing after
                                # the pending key has been cleared by webhook


def _key(slug: str) -> str:
    return f"halo:pending_onboard:{slug}"


def _started_key(slug: str) -> str:
    """Separate from the pending key so it survives clear_pending(slug). Used
    by AlarmWaitOnlineView to compute `time_to_register_seconds` for the
    response, which the iPhone uses to correlate its own timing."""
    return f"halo:onboard_started_at:{slug}"


def mark_pending(slug: str) -> None:
    """Called by HaloOnboardPayloadView. Idempotent — re-marking refreshes TTL.

    Build 156 item 6: also stamp a started_at Redis key (longer TTL) so
    AlarmWaitOnlineView can compute time_to_register_seconds in its response.
    Re-marking refreshes the started_at too, so retries reset the clock.
    """
    import time
    now = int(time.time())
    _redis_client.set(_key(slug), "1", ex=PENDING_TTL_SEC)
    _redis_client.set(_started_key(slug), str(now), ex=ONBOARD_STARTED_TTL_SEC)
    logger.info(
        "halo_pending_marked slug=%s ttl=%ds started_at=%d",
        slug, PENDING_TTL_SEC, now,
    )


def is_pending(slug: str) -> bool:
    """Called by HaloRegisterWebhookView before any DB mutation."""
    return bool(_redis_client.exists(_key(slug)))


def clear_pending(slug: str) -> None:
    """Called after successful AlarmDevice create. Subsequent heartbeats from
    the same Halo will hit the not_pending guard and 403 silently — that's
    expected; the row already exists so there's nothing to do.

    Note: clears ONLY the pending key. The started_at key persists for its
    own TTL so wait-online can still compute time_to_register_seconds.
    """
    _redis_client.delete(_key(slug))


def get_onboard_started_at(slug: str) -> "int | None":
    """Returns the Unix timestamp captured by mark_pending, or None if the
    key has expired or was never set. Used by AlarmWaitOnlineView to compute
    time_to_register_seconds for the response body.
    """
    raw = _redis_client.get(_started_key(slug))
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def health_check() -> bool:
    """Surface Redis availability — used by the onboard-payload view to fail
    fast if Redis is down rather than mid-flow."""
    try:
        _redis_client.ping()
        return True
    except Exception:
        return False
