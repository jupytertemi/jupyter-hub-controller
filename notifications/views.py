import logging
import os
import time
import uuid

from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status
from rest_framework.throttling import AnonRateThrottle

from .apns_client import send_apns_push
from .serializers import APNsTokenRegisterSerializer
from .token_store import read_store, register_token, remove_device, remove_stale_tokens

logger = logging.getLogger(__name__)


def _read_apns_credentials():
    """Read APNs auth env vars at request time (not import time) so a hot
    rotation of the .p8 key picks up without a server restart. Returns the
    tuple Django needs to call apns_client.send_apns_push, or None if the
    hub isn't APNs-configured yet.
    """
    try:
        bundle_id = os.environ["APNS_BUNDLE_ID"]
        team_id = os.environ["APNS_TEAM_ID"]
        key_id = os.environ["APNS_KEY_ID"]
        key_path = os.environ["APNS_PRIVATE_KEY_PATH"]
        with open(key_path) as f:
            private_key = f.read()
        return {
            "bundle_id": bundle_id,
            "team_id": team_id,
            "key_id": key_id,
            "private_key": private_key,
        }
    except (KeyError, FileNotFoundError) as e:
        logger.error("APNs credentials missing: %s", e)
        return None


class APNsTokenRegisterView(APIView):
    """POST /api/devices/apns-token

    iOS app calls this at startup (after notification permission granted) AND
    on FirebaseMessaging.onTokenRefresh. Body:

        {
          "device_token": "abc123...",
          "device_id":    "iPhone-of-Temi-uuid",
          "bundle_id":    "com.app.jupyter.dev",
          "environment":  "sandbox" | "production",
          "platform":     "ios"   (default; reserved for future android)
        }

    Returns 201 on insert, 200 on update, 400 on validation error.
    Authenticated via the existing hub auth (HUB_SECRET / DRF default).
    """
    permission_classes = []

    def post(self, request):
        ser = APNsTokenRegisterSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        existed = d["device_id"] in read_store()
        register_token(
            device_id=d["device_id"],
            apns_token=d["device_token"],
            environment=d["environment"],
            bundle_id=d["bundle_id"],
            platform=d["platform"],
        )
        logger.info(
            "APNs token registered: device_id=%s env=%s platform=%s (%s)",
            d["device_id"], d["environment"], d["platform"],
            "updated" if existed else "new",
        )
        return Response(
            {"status": "registered", "updated": existed},
            status=status.HTTP_200_OK if existed else status.HTTP_201_CREATED,
        )


class APNsTokenDeleteView(APIView):
    """DELETE /api/devices/apns-token/<device_id>

    Best-effort de-registration when the user logs out / uninstalls
    intentionally. Apple's 410-Unregistered cleanup in the publisher is the
    authoritative path; this endpoint just keeps the store tidy when the app
    has a chance to cooperate.
    """
    permission_classes = []

    def delete(self, request, device_id):
        removed = remove_device(device_id)
        if removed:
            logger.info("APNs token removed: device_id=%s", device_id)
            return Response({"status": "deleted"}, status=status.HTTP_200_OK)
        return Response(
            {"status": "not_found"},
            status=status.HTTP_404_NOT_FOUND,
        )


class APNsTokenListView(APIView):
    """GET /api/devices/apns-token

    Returns the current store (token values redacted to 8-char prefix +
    suffix). Used by the support console / Helios dashboard / for
    debugging — never returns the full token to the client.
    """
    permission_classes = []

    def get(self, request):
        store = read_store()
        redacted = {
            dev_id: {
                **meta,
                "apns_token": (
                    meta["apns_token"][:8] + "…" + meta["apns_token"][-8:]
                    if meta.get("apns_token") else ""
                ),
            }
            for dev_id, meta in store.items()
        }
        return Response({"count": len(redacted), "devices": redacted})


class _TestPushThrottle(AnonRateThrottle):
    """12 requests/min (= 1 every 5s) per source IP. Diagnostic endpoint is
    inherently low-volume; this is abuse-protection only, per spec §3."""
    scope = "test_apns_push"
    rate = "12/min"


class TestApnsPushView(APIView):
    """POST /api/system/test-apns-push

    Diagnostic endpoint: fires an APNs banner to all (or one specific)
    registered device(s) so the Flutter "Notifications diagnostic" page
    can prove on-device that the direct-APNs path is alive without
    waiting for a real AI event.

    Body (all optional):
        device_id  → if provided, push only to this device
        title      → defaults to "Jupyter test push"
        body       → defaults to "If you see this, banner alerts are working."

    Returns 200 with per-target latency + result. Reuses
    notifications.apns_client.send_apns_push so the test exercises the
    same code path real events use. Stale tokens (410 / 400 BadDeviceToken)
    are auto-cleaned from the store + .env, mirroring production behaviour.

    Spec: jupyter-helios-web/docs/BACKEND_TEST_PUSH_ENDPOINT.md
    """
    permission_classes = []  # match project convention; HAProxy guards perimeter
    throttle_classes = [_TestPushThrottle]

    DEFAULT_TITLE = "Jupyter test push"
    DEFAULT_BODY = "If you see this, banner alerts are working."

    def post(self, request):
        creds = _read_apns_credentials()
        if not creds:
            return Response(
                {"detail": "APNs not configured on this hub"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        body_in = request.data or {}
        device_id = body_in.get("device_id")
        title = body_in.get("title") or self.DEFAULT_TITLE
        body = body_in.get("body") or self.DEFAULT_BODY

        store = read_store()
        if device_id:
            if device_id not in store:
                return Response(
                    {"detail": "device not registered"},
                    status=status.HTTP_404_NOT_FOUND,
                )
            targets = {device_id: store[device_id]}
        else:
            targets = dict(store)

        if not targets:
            return Response({
                "targets": [],
                "ok_count": 0,
                "fail_count": 0,
                "total_latency_ms": 0,
                "detail": "No registered devices. Open the app on your phone first.",
            })

        diagnostic_event_id = f"diagnostic-test-{uuid.uuid4()}"
        payload = {
            "aps": {
                "alert": {"title": title, "body": body},
                "sound": "default",
                "badge": 1,
            },
            "extra": {"event_id": diagnostic_event_id, "kind": "diagnostic"},
        }

        results = []
        stale_tokens = []
        ok_count = 0
        fail_count = 0
        t0_total = time.perf_counter()

        for dev_id, meta in targets.items():
            tok = meta["apns_token"]
            env = meta.get("environment", "production")
            push_result = send_apns_push(
                token=tok,
                payload=payload,
                push_type="alert",
                topic=creds["bundle_id"],
                team_id=creds["team_id"],
                key_id=creds["key_id"],
                private_key=creds["private_key"],
                environment=env,
            )
            entry = {
                "device_id": dev_id,
                "token_last8": tok[-8:],
                "environment": env,
                "result": (
                    "stale_token" if push_result["result"] == "stale"
                    else push_result["result"]
                ),
                "http_status": push_result["http_status"],
                "latency_ms": push_result["latency_ms"],
            }
            if push_result.get("reason"):
                entry["reason"] = push_result["reason"]
            results.append(entry)
            if push_result["result"] == "ok":
                ok_count += 1
            else:
                fail_count += 1
            if push_result["result"] == "stale":
                stale_tokens.append(tok)

        # Mirror production behaviour: clean up stale tokens immediately.
        if stale_tokens:
            removed = remove_stale_tokens(stale_tokens)
            logger.info(
                "diagnostic test push removed %d stale tokens", removed,
            )

        total_ms = int((time.perf_counter() - t0_total) * 1000)
        logger.info(
            "test-apns-push: targets=%d ok=%d fail=%d total_ms=%d",
            len(results), ok_count, fail_count, total_ms,
        )
        return Response({
            "targets": results,
            "ok_count": ok_count,
            "fail_count": fail_count,
            "total_latency_ms": total_ms,
            "diagnostic_event_id": diagnostic_event_id,
        })
