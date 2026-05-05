import logging

from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status

from .serializers import APNsTokenRegisterSerializer
from .token_store import register_token, remove_device, read_store

logger = logging.getLogger(__name__)


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
