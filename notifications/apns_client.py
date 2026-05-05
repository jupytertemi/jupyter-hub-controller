"""Pure-Python APNs HTTP/2 sender. Imported by both the standalone
live_activity_publisher.py and the Django diagnostic endpoint so the two
paths exercise identical code (no drift). No Django imports here — the
publisher process bootstraps without Django and must be able to use this.

If you're touching the APNs send path, change it ONCE here. Both consumers
inherit the change.
"""
import json
import os
import time

import httpx
import jwt


APNS_BASE_PRODUCTION = "https://api.push.apple.com"
APNS_BASE_SANDBOX = "https://api.sandbox.push.apple.com"


def apns_base_url(environment):
    """Endpoint URL per environment. Unknown values fall back to production
    (worst case = BadDeviceToken → caller's stale-cleanup path runs)."""
    return APNS_BASE_SANDBOX if environment == "sandbox" else APNS_BASE_PRODUCTION


def _build_jwt(team_id, key_id, private_key):
    return jwt.encode(
        {"iss": team_id, "iat": int(time.time())},
        private_key,
        algorithm="ES256",
        headers={"kid": key_id},
    )


def send_apns_push(
    token,
    payload,
    push_type,
    topic,
    *,
    team_id,
    key_id,
    private_key,
    environment="production",
    timeout=10.0,
    max_retries=2,
):
    """Send one APNs push. Returns dict:
        {
            "result": "ok" | "stale" | "fail" | "skipped",
            "http_status": int | "exception" | None,
            "reason": str,
            "latency_ms": int,
            "url": str,
        }
    "stale" specifically means the token is dead per Apple's documented
    codes (410 Unregistered, 400 BadDeviceToken). Caller should remove it.

    Retries 5xx and transport errors with backoff [0.5s, 2s, 5s] up to
    max_retries times. Never raises — always returns a result dict.
    """
    if not token:
        return {
            "result": "skipped", "http_status": None,
            "reason": "no token", "latency_ms": 0,
            "url": apns_base_url(environment),
        }

    base_url = apns_base_url(environment)
    headers = {
        "authorization": f"bearer {_build_jwt(team_id, key_id, private_key)}",
        "apns-push-type": push_type,
        "apns-topic": topic,
        "apns-priority": "10",
    }
    backoffs = [0.5, 2.0, 5.0]
    last_status = None
    last_reason = ""
    t0 = time.perf_counter()

    for attempt in range(max_retries + 1):
        try:
            with httpx.Client(http2=True, timeout=timeout) as c:
                r = c.post(f"{base_url}/3/device/{token}",
                           headers=headers, json=payload)
            last_status = r.status_code
            try:
                last_reason = (r.json().get("reason") if r.text else "") or ""
            except Exception:
                last_reason = ""
            if r.status_code == 200:
                return {
                    "result": "ok", "http_status": 200,
                    "reason": "", "latency_ms": int((time.perf_counter() - t0) * 1000),
                    "url": base_url,
                }
            if r.status_code == 410 or (r.status_code == 400 and last_reason == "BadDeviceToken"):
                return {
                    "result": "stale", "http_status": r.status_code,
                    "reason": last_reason or "Unregistered",
                    "latency_ms": int((time.perf_counter() - t0) * 1000),
                    "url": base_url,
                }
            if 500 <= r.status_code < 600 and attempt < max_retries:
                time.sleep(backoffs[attempt])
                continue
            break
        except httpx.HTTPError as e:
            last_status = "exception"
            last_reason = str(e)[:200]
            if attempt < max_retries:
                time.sleep(backoffs[attempt])
                continue
            break
        except Exception as e:
            return {
                "result": "fail", "http_status": "exception",
                "reason": str(e)[:200],
                "latency_ms": int((time.perf_counter() - t0) * 1000),
                "url": base_url,
            }
    return {
        "result": "fail", "http_status": last_status,
        "reason": last_reason,
        "latency_ms": int((time.perf_counter() - t0) * 1000),
        "url": base_url,
    }
