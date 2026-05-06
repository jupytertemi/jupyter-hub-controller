import logging
import subprocess

from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView


class SystemTemperatureView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        try:
            with open('/sys/class/thermal/thermal_zone1/temp', 'r') as f:
                temp_milli = int(f.read().strip())
            temp_celsius = temp_milli / 1000.0
            return Response({
                'success': True,
                'temperature': round(temp_celsius, 1),
                'unit': 'celsius'
            })
        except Exception as e:
            return Response({
                'success': False,
                'error': str(e)
            }, status=500)


class SystemUptimeView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        try:
            with open('/proc/uptime', 'r') as f:
                uptime_seconds = float(f.read().split()[0])
            return Response({
                'success': True,
                'uptime': int(uptime_seconds),
                'unit': 'seconds'
            })
        except Exception as e:
            return Response({
                'success': False,
                'error': str(e)
            }, status=500)


class _SystemRestartThrottle(SimpleRateThrottle):
    """Always throttles by remote IP, regardless of auth status.

    AnonRateThrottle won't apply here because HAProxy injects HUB_BASIC_AUTH
    upstream, so DRF sees every request as authenticated. We want a hard
    1/min cap on this destructive endpoint either way.
    """
    scope = "system_restart"

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }


class _SystemRestartSerializer(serializers.Serializer):
    scope = serializers.ChoiceField(choices=["services", "host"])
    confirm = serializers.BooleanField()

    def validate_confirm(self, value):
        if value is not True:
            raise serializers.ValidationError(
                "confirm must be true to actually restart."
            )
        return value


class SystemRestartView(APIView):
    """POST /api/system/restart (Helios Tier 1 §3.3).

    Body: {scope: "services"|"host", confirm: true}

    scope=services → `docker compose restart` (whole stack, takes ~30-60s)
    scope=host     → `reboot` (full board reboot, takes ~90s to come back)

    Returns 202 with {will_restart_in_seconds: 5} so the response flies back
    before the actual restart kills this very process. Throttled to 1/min via
    the system_restart scope (settings/common.py) — accidental retries can't
    repeatedly bounce the hub.
    """
    permission_classes = [AllowAny]
    throttle_classes = [_SystemRestartThrottle]

    def post(self, request):
        ser = _SystemRestartSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        scope = ser.validated_data["scope"]

        delay_seconds = 5
        if scope == "services":
            cmd = (
                f"sleep {delay_seconds} && "
                f"cd /root/jupyter-container && "
                f"sudo docker compose restart"
            )
        else:
            cmd = f"sleep {delay_seconds} && sudo reboot"

        try:
            subprocess.Popen(
                ["sh", "-c", cmd],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as e:
            logging.exception("system restart spawn failed: %s", e)
            return Response(
                {"error": "Failed to schedule restart."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        logging.warning("system restart scheduled: scope=%s in %ss", scope, delay_seconds)
        return Response(
            {"queued": True, "scope": scope, "will_restart_in_seconds": delay_seconds},
            status=status.HTTP_202_ACCEPTED,
        )
