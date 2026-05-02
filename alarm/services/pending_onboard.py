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


def _key(slug: str) -> str:
    return f"halo:pending_onboard:{slug}"


def mark_pending(slug: str) -> None:
    """Called by HaloOnboardPayloadView. Idempotent — re-marking refreshes TTL."""
    _redis_client.set(_key(slug), "1", ex=PENDING_TTL_SEC)
    logger.info("halo_pending_marked slug=%s ttl=%ds", slug, PENDING_TTL_SEC)


def is_pending(slug: str) -> bool:
    """Called by HaloRegisterWebhookView before any DB mutation."""
    return bool(_redis_client.exists(_key(slug)))


def clear_pending(slug: str) -> None:
    """Called after successful AlarmDevice create. Subsequent heartbeats from
    the same Halo will hit the not_pending guard and 403 silently — that's
    expected; the row already exists so there's nothing to do."""
    _redis_client.delete(_key(slug))


def health_check() -> bool:
    """Surface Redis availability — used by the onboard-payload view to fail
    fast if Redis is down rather than mid-flow."""
    try:
        _redis_client.ping()
        return True
    except Exception:
        return False
