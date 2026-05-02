"""Live Activity 2FA confirm / cancel callbacks.

When the admin taps "Confirm Reset" or "Cancel" on the Live Activity card,
the Flutter app calls one of these endpoints. The hub then publishes the
corresponding command on `/{slug}/recovery` for the Halo to act on.

Auth: hub-internal Basic auth (same as every other /api/* endpoint). The
2FA security boundary is enforced by the firmware itself — the hub can't
forge a confirmation without a valid nonce that the Halo just issued, and
the Halo's nonce expires in 60s.
"""
from __future__ import annotations

import logging

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from alarm.services.halo_recovery import (
    cancel_factory_reset,
    confirm_factory_reset,
)

logger = logging.getLogger(__name__)


class HaloRecoveryConfirmView(APIView):
    """POST body: {"slug": "jupyter-alarm-XXXXXX", "nonce": <int>}"""

    def post(self, request):
        slug = request.data.get("slug")
        nonce = request.data.get("nonce")
        if not slug or nonce is None:
            return Response(
                {"detail": "slug and nonce are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            nonce_int = int(nonce)
        except (TypeError, ValueError):
            return Response(
                {"detail": "nonce must be an integer"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        published = confirm_factory_reset(slug, nonce_int)
        logger.info("halo_recovery_confirm slug=%s nonce=%s ok=%s",
                    slug, nonce_int, published)
        return Response(
            {"published": published},
            status=status.HTTP_200_OK if published else status.HTTP_502_BAD_GATEWAY,
        )


class HaloRecoveryCancelView(APIView):
    """POST body: {"slug": "jupyter-alarm-XXXXXX"}"""

    def post(self, request):
        slug = request.data.get("slug")
        if not slug:
            return Response(
                {"detail": "slug is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        published = cancel_factory_reset(slug)
        logger.info("halo_recovery_cancel slug=%s ok=%s", slug, published)
        return Response(
            {"published": published},
            status=status.HTTP_200_OK if published else status.HTTP_502_BAD_GATEWAY,
        )
