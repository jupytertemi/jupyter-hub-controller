"""Celery tasks for the notifications app.

Today: one task — `cleanup_unused_apns_tokens`. Runs daily at 04:30 to
remove APNs tokens whose last_seen_at is older than 30 days. This is a
safety net for the case where a customer uninstalls the app without
generating a 410 from Apple (e.g., they uninstalled but never had a real
event push to them, so the in-band 410-cleanup in push_apns_alert never
fired). 30 days is a long enough idle window that any token still in
the store has clearly been abandoned.

Beat schedule registration is handled by Django's startup hook (see
notifications/apps.py).
"""
import logging

from celery import shared_task

from .token_store import cleanup_unused_tokens

logger = logging.getLogger(__name__)


@shared_task
def cleanup_unused_apns_tokens(stale_after_days=30):
    """Remove APNs tokens not refreshed via /api/devices/apns-token in
    `stale_after_days` days. Returns the number removed for logging.
    """
    try:
        removed = cleanup_unused_tokens(stale_after_days=stale_after_days)
        if removed:
            logger.info(
                "apns safety-sweep removed %d unused tokens (>%d days idle)",
                removed, stale_after_days,
            )
        return removed
    except Exception as e:
        logger.exception("apns safety-sweep failed: %s", e)
        raise
