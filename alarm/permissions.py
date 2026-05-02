"""Permission classes for the v1.6 Halo onboard endpoints."""
from rest_framework.permissions import BasePermission


class LocalOnly(BasePermission):
    """Only permit calls from localhost / docker bridge.

    HAProxy + the hub's network setup ensure ``/api/internal/*`` can't
    be reached from outside the hub's docker bridge. This is a defence-
    in-depth check at the Django layer.

    Acceptable source IPs:
      * 127.0.0.0/8  (loopback)
      * 172.16.0.0/12 (default Docker bridge ranges)
      * 192.168.0.0/16 (some compose networks; typical home LAN — caller
        must already be on the hub's docker network for HAProxy to route
        the /api/internal/* path here, so trusting this range is safe in
        practice)
    """

    LOCAL_PREFIXES = ("127.", "172.", "192.168.", "10.")

    def has_permission(self, request, view):
        ip = self._client_ip(request)
        if not ip:
            return False
        return any(ip.startswith(p) for p in self.LOCAL_PREFIXES)

    def _client_ip(self, request) -> str:
        # Prefer X-Forwarded-For (HAProxy sets this when proxying)
        xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if xff:
            return xff.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR", "")
