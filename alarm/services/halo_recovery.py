"""Halo factory reset 2FA flow over MQTT.

Per HALO_2FA_FACTORY_RESET_BACKEND_BRIEF.md (firmware v2.19.1+):
factory reset now requires two-factor authentication. The hub publishes
`factory_reset` with the device's NVS-stored secret, the Halo replies with
a one-time nonce on `/recovery/status`, and the human admin must tap
"Confirm" on a Live Activity card on their phone within 60 seconds.

The previous unauthenticated paths (`HTTP /factoryreset`, TCP socket
`action: factory_reset`, Argus cloud command) are blocked in firmware ≥
v2.19.1 because any LAN attacker could brick every Halo in a building.

This module is the synchronous half of the flow:
  * `initiate_factory_reset(slug, secret)` publishes the request and
    waits up to ~5s for the Halo's `pending` response (returns the nonce
    so the caller can fire a Live Activity push).
  * `confirm_factory_reset(slug, nonce)` and `cancel_factory_reset(slug)`
    are fire-and-forget — they publish the human's decision after the
    Live Activity callback.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Optional

import paho.mqtt.client as mqtt
from django.conf import settings

logger = logging.getLogger(__name__)

# Sub-topics (per firmware brief)
RECOVERY_TOPIC = "/{slug}/recovery"
RECOVERY_STATUS_TOPIC = "/{slug}/recovery/status"

# Internal hub topic — live_activity_publisher subscribes here and fires
# the APNs Live Activity push for the admin's iPhone.
LA_OFFBOARD_PENDING_TOPIC = "/halo_offboard_2fa_pending"


def _connect() -> mqtt.Client:
    c = mqtt.Client(
        client_id=f"hub-recovery-{int(time.time() * 1000)}",
        clean_session=True,
    )
    c.username_pw_set(settings.MQTT_USERNAME, settings.MQTT_PASSWORD)
    c.connect(settings.MQTT_HOST, settings.MQTT_PORT, keepalive=30)
    return c


def initiate_factory_reset(
    slug: str, device_secret: str, timeout: float = 5.0,
) -> Optional[dict]:
    """Publish factory_reset, wait for `pending` response, return its parsed
    payload. Returns None on timeout or any `denied` response.

    The Halo's `pending` response contains:
        {"factory_reset": "pending", "nonce": <uint32>,
         "expires_in": 60, "serial": "JUP-OUTDR-XXXXXX"}
    """
    if not slug or not device_secret:
        logger.warning("halo_factory_reset: missing slug or secret")
        return None

    cmd_topic = RECOVERY_TOPIC.format(slug=slug)
    status_topic = RECOVERY_STATUS_TOPIC.format(slug=slug)
    response: list[dict] = []

    def _on_message(_client, _ud, msg):
        try:
            response.append(json.loads(msg.payload.decode("utf-8")))
        except Exception:  # malformed payload — ignore
            pass

    client = _connect()
    client.on_message = _on_message
    client.subscribe(status_topic, qos=1)
    client.loop_start()
    # Brief settling so the subscribe lands before we publish — otherwise
    # an instant Halo response could fire before our subscription is alive.
    time.sleep(0.3)

    # Cancel-first: clear any stuck pending state on the firmware side
    # before issuing a fresh factory_reset. Per session 2026-05-03 testing,
    # if a previous offboard's confirm silently dropped, the firmware can
    # be left with a stale `factory_reset_pending` flag + nonce that
    # poisons the next attempt with `denied/invalid_nonce` despite the new
    # nonce just having been issued. Cancel resets that state cleanly.
    # Fire-and-forget — no need to wait for `cancelled` response, the
    # firmware processes the message order before factory_reset.
    cancel_payload = json.dumps({"command": "cancel_factory_reset"})
    client.publish(cmd_topic, cancel_payload, qos=1).wait_for_publish(timeout=1.0)
    time.sleep(0.4)  # let firmware process the cancel before factory_reset
    # Drain any `cancelled` response so it doesn't get treated as the
    # pending response we're about to look for.
    response.clear()

    payload = json.dumps({"command": "factory_reset", "secret": device_secret})
    client.publish(cmd_topic, payload, qos=1).wait_for_publish(timeout=2.0)
    logger.info("halo_factory_reset published slug=%s (cancel-first)", slug)

    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if response:
                resp = response[0]
                state = resp.get("factory_reset")
                if state == "pending":
                    logger.info(
                        "halo_factory_reset pending slug=%s nonce=%s serial=%s",
                        slug, resp.get("nonce"), resp.get("serial"),
                    )
                    return resp
                logger.warning(
                    "halo_factory_reset rejected slug=%s state=%s reason=%s",
                    slug, state, resp.get("reason"),
                )
                return None
            time.sleep(0.1)
        logger.warning("halo_factory_reset timeout slug=%s", slug)
        return None
    finally:
        client.loop_stop()
        try:
            client.disconnect()
        except Exception:
            pass


def factory_reset_with_verify(
    slug: str, device_secret: str, max_attempts: int = 2,
    pending_timeout: float = 5.0, confirmed_timeout: float = 5.0,
) -> dict:
    """Full reliable factory_reset flow with cancel-first + verify-confirmed + retry.

    Per session 2026-05-03 firmware reliability findings (see docs/halo-2fa-
    factory-reset-firmware-bug-2026-05-03.md), the v2.21.0 firmware can:
      * Silently drop a confirm_factory_reset (state machine bug after a
        previous offboard's confirm partially-failed)
      * Reply `denied/invalid_nonce` for the same nonce it just issued
        (stale internal nonce after a stuck pending state)
      * Stop responding to recovery topic entirely (MQTT subsystem hang)

    This function defends against all three by:
      1. Always sending `cancel_factory_reset` first to clear stale state
      2. Verifying the firmware actually replies `confirmed/resetting`
         (not just trusting the publish-PUBACK from EMQX)
      3. Retrying the whole sequence up to `max_attempts` if confirm
         fails or the firmware goes silent

    Returns a dict with at minimum `success: bool` and `attempts: int`.
    On success: `{success: True, nonce, serial, attempts}`.
    On failure: `{success: False, reason: <str>, attempts, last_pending}`.
    """
    if not slug or not device_secret:
        return {"success": False, "reason": "missing_input", "attempts": 0}

    cmd_topic = RECOVERY_TOPIC.format(slug=slug)
    status_topic = RECOVERY_STATUS_TOPIC.format(slug=slug)

    last_reason = None
    last_nonce = None
    last_serial = None

    for attempt in range(1, max_attempts + 1):
        responses: list[dict] = []
        lock = threading.Lock()

        def _on_message(_client, _ud, msg):
            try:
                data = json.loads(msg.payload.decode("utf-8"))
            except Exception:
                return
            with lock:
                responses.append(data)

        client = _connect()
        client.on_message = _on_message
        client.subscribe(status_topic, qos=1)
        client.loop_start()
        time.sleep(0.3)  # subscribe-settle window

        try:
            # Step 1 — cancel-first: clear any stuck firmware pending state
            cancel = json.dumps({"command": "cancel_factory_reset"})
            client.publish(cmd_topic, cancel, qos=1).wait_for_publish(timeout=1.0)
            time.sleep(0.4)
            with lock:
                responses.clear()  # don't treat `cancelled` as the pending we'll look for

            # Step 2 — publish factory_reset
            fr = json.dumps({"command": "factory_reset", "secret": device_secret})
            client.publish(cmd_topic, fr, qos=1).wait_for_publish(timeout=2.0)
            logger.info(
                "halo_factory_reset_with_verify attempt=%d slug=%s — published factory_reset",
                attempt, slug,
            )

            # Step 3 — wait for pending (or denied/silent)
            pending = _wait_first_response(responses, lock, pending_timeout)
            if not pending:
                last_reason = "firmware_silent_no_pending"
                logger.warning(
                    "halo_factory_reset_with_verify attempt=%d slug=%s — no pending in %.0fs (firmware MQTT silent)",
                    attempt, slug, pending_timeout,
                )
                continue
            state = pending.get("factory_reset")
            if state == "denied":
                reason = pending.get("reason", "unknown")
                last_reason = f"denied:{reason}"
                logger.warning(
                    "halo_factory_reset_with_verify attempt=%d slug=%s — denied at pending step: %s",
                    attempt, slug, reason,
                )
                # invalid_secret is permanent — no retry will fix it
                if reason == "invalid_secret":
                    return {"success": False, "reason": last_reason,
                            "attempts": attempt}
                continue  # try cancel-first cycle again

            if state != "pending":
                last_reason = f"unexpected_state:{state}"
                logger.warning(
                    "halo_factory_reset_with_verify attempt=%d slug=%s — unexpected state %r",
                    attempt, slug, state,
                )
                continue

            last_nonce = pending.get("nonce")
            last_serial = pending.get("serial")
            logger.info(
                "halo_factory_reset_with_verify attempt=%d slug=%s — pending nonce=%s serial=%s",
                attempt, slug, last_nonce, last_serial,
            )

            # Step 4 — publish confirm_factory_reset; clear responses first
            with lock:
                responses.clear()
            cf = json.dumps({"command": "confirm_factory_reset",
                             "nonce": int(last_nonce)})
            client.publish(cmd_topic, cf, qos=1).wait_for_publish(timeout=2.0)
            logger.info(
                "halo_factory_reset_with_verify attempt=%d slug=%s — published confirm nonce=%s",
                attempt, slug, last_nonce,
            )

            # Step 5 — VERIFY: wait for `confirmed/resetting` (not just PUBACK)
            confirmed_resp = _wait_first_response(responses, lock,
                                                  confirmed_timeout)
            if not confirmed_resp:
                last_reason = "firmware_silent_no_confirmed"
                logger.warning(
                    "halo_factory_reset_with_verify attempt=%d slug=%s — confirm publish OK but no confirmed/resetting in %.0fs (firmware silently dropped confirm)",
                    attempt, slug, confirmed_timeout,
                )
                continue
            cstate = confirmed_resp.get("factory_reset")
            if cstate == "confirmed":
                logger.info(
                    "halo_factory_reset_with_verify attempt=%d slug=%s — VERIFIED confirmed/resetting (Halo wiping NVS now)",
                    attempt, slug,
                )
                return {
                    "success": True,
                    "nonce": last_nonce,
                    "serial": last_serial,
                    "attempts": attempt,
                }
            if cstate == "denied":
                reason = confirmed_resp.get("reason", "unknown")
                last_reason = f"confirm_denied:{reason}"
                logger.warning(
                    "halo_factory_reset_with_verify attempt=%d slug=%s — confirm denied: %s (likely firmware nonce-state bug — retrying with cancel-first)",
                    attempt, slug, reason,
                )
                continue
            last_reason = f"unexpected_confirmed_state:{cstate}"
            logger.warning(
                "halo_factory_reset_with_verify attempt=%d slug=%s — unexpected post-confirm state %r",
                attempt, slug, cstate,
            )

        finally:
            client.loop_stop()
            try:
                client.disconnect()
            except Exception:
                pass

    return {
        "success": False,
        "reason": last_reason or "unknown",
        "attempts": max_attempts,
        "last_nonce": last_nonce,
        "last_serial": last_serial,
    }


def _wait_first_response(
    responses: list, lock: threading.Lock, timeout: float,
) -> Optional[dict]:
    """Block up to `timeout` for ANY response to land in `responses`.
    Returns the first one (and pops nothing — caller may want to read others)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with lock:
            if responses:
                return responses[0]
        time.sleep(0.1)
    return None


def confirm_factory_reset(slug: str, nonce: int) -> bool:
    """Publish confirm_factory_reset with the nonce the Halo issued. Returns
    True if the publish succeeded (not whether the Halo accepted it — the
    accept/reject answer arrives later on /recovery/status)."""
    if not slug or not nonce:
        return False
    payload = json.dumps({"command": "confirm_factory_reset", "nonce": int(nonce)})
    return _publish_one(RECOVERY_TOPIC.format(slug=slug), payload)


def cancel_factory_reset(slug: str) -> bool:
    """Publish cancel_factory_reset. Halo will move out of pending state
    and respond with `cancelled` on /recovery/status."""
    if not slug:
        return False
    payload = json.dumps({"command": "cancel_factory_reset"})
    return _publish_one(RECOVERY_TOPIC.format(slug=slug), payload)


def publish_offboard_2fa_pending(
    slug: str, alarm_id: int, nonce: int, serial: str, expires_in: int,
) -> bool:
    """Internal-hub trigger for the Live Activity APNs push.

    live_activity_publisher.py subscribes to LA_OFFBOARD_PENDING_TOPIC and
    fires the iOS APNs push-to-start using the admin's per-owner LA token.
    """
    payload = json.dumps({
        "slug": slug,
        "alarm_id": alarm_id,
        "nonce": int(nonce),
        "serial": serial,
        "expires_at": int(time.time()) + int(expires_in),
        "expires_in": int(expires_in),
        "title": "Factory Reset Requested",
        "body": f"Halo {serial} will be wiped and reset to factory defaults.",
    })
    return _publish_one(LA_OFFBOARD_PENDING_TOPIC, payload)


def _publish_one(topic: str, payload: str, timeout: float = 3.0) -> bool:
    """Connect → loop_start (paho-mqtt needs the loop running to receive
    PUBACK for QoS 1) → publish → wait_for_publish → loop_stop → disconnect."""
    client = None
    try:
        client = _connect()
        client.loop_start()
        info = client.publish(topic, payload, qos=1)
        info.wait_for_publish(timeout=timeout)
        return info.is_published()
    except Exception as exc:
        logger.warning("mqtt_publish_failed topic=%s err=%s", topic, exc)
        return False
    finally:
        if client is not None:
            try:
                client.loop_stop()
                client.disconnect()
            except Exception:
                pass
