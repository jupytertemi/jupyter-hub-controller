import json
import logging
import time

from django.conf import settings
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.generics import (
    GenericAPIView,
    ListCreateAPIView,
    RetrieveUpdateDestroyAPIView,
    DestroyAPIView,
    get_object_or_404,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from alarm.models import AlarmDevice
from alarm.serializers import (
    AlarmDeviceSerializer,
    AlarmModeSerializer,
    TurnOnOffAlarmSerializer,
    UpdateAlarmDeviceSerializer,
)
from core.pagination import Pagination
from utils.hass_client import HassClient
from utils.mqtt_client import MQTTClient
from utils.socket_publisher import publish_socket_message
from utils.token_generate import HasFRVApiKey


class ListCreateAlarmDeviceView(ListCreateAPIView):
    model = AlarmDevice
    serializer_class = AlarmDeviceSerializer
    queryset = AlarmDevice.objects.all()
    pagination_class = Pagination
    filter_backends = [DjangoFilterBackend, OrderingFilter, SearchFilter]
    # 2026-05-03 — `identity_name` added for Flutter Build 156 late-register
    # fallback: when wait-online 408s, the rebuild GETs /alarms?identity_name=<slug>
    # to check whether the row was created sub-second-late. Server-side filter
    # avoids paginating the full list client-side.
    filterset_fields = ["type", "identity_name"]
    ordering_fields = ["created_at"]
    search_fields = ["name"]


class RetrieveDeleteAlarmDeviceView(RetrieveUpdateDestroyAPIView):
    model = AlarmDevice
    serializer_class = UpdateAlarmDeviceSerializer
    lookup_field = "id"
    queryset = AlarmDevice.objects.all()

    def destroy(self, request, *args, **kwargs):
        """Offboard a Halo.

        Per HALO_2FA_FACTORY_RESET_BACKEND_BRIEF.md (firmware ≥ v2.19.1) the
        unauthenticated factory-reset paths (HTTP /factoryreset, TCP socket
        action: factory_reset, Argus command) are blocked. The only way to
        wipe the Halo's NVS is the MQTT 2FA flow:

            1. Hub publishes `factory_reset` + device_secret to /{slug}/recovery
            2. Halo replies `pending` with a one-time nonce
            3. Hub fires a Live Activity push to admin's phone
            4. Admin taps Confirm → hub publishes `confirm_factory_reset` + nonce
            5. Halo wipes NVS, reboots to AP mode

        Per product decision: hub-side cleanup (DB row, HA scripts/automations)
        runs UNCONDITIONALLY. If the admin doesn't confirm within 60s, the
        Halo's own internal timer auto-cancels. The Halo stays bonded to
        nothing (its hub config still points at us, but our DB row is gone),
        which is acceptable — we'd rather a stranded Halo than a stranded
        hub-side row.
        """
        instance = self.get_object()
        identity_name = instance.identity_name
        device_secret = instance.device_secret  # v1.6 column
        alarm_id = instance.id

        # SEQUENCING (critical):
        #   1. Initiate 2FA factory reset SYNCHRONOUSLY (publish + wait ≤5s
        #      for pending nonce). MQTT broker + live_activity_publisher
        #      are still alive at this point, so the publish reliably
        #      lands and the LA push reliably fires.
        #   2. ONLY THEN run perform_destroy() (HA scripts, automations,
        #      DB row). If we did this first, gunicorn-worker-death mid
        #      cleanup would drop the 2FA thread before publish landed.
        #
        # Worst case: Halo offline → 5s block, no pending response, no LA
        # push, hub-side cleanup proceeds anyway. Halo will need physical
        # reset. This is the documented behavior per the firmware brief.
        # Verified-confirm offboard flow with cancel-first + retry.
        # See alarm/services/halo_recovery.py::factory_reset_with_verify
        # and docs/halo-2fa-factory-reset-firmware-bug-2026-05-03.md for
        # rationale. The hub publishes cancel → factory_reset → confirm,
        # then verifies the firmware actually replied `confirmed/resetting`
        # before declaring success. If confirm is silently dropped (real
        # v2.21.0 firmware bug we hit in testing), the chain retries up to
        # 2 attempts with cancel-first to clear stale state.
        factory_reset_confirmed = False
        result_reason = None
        attempts = 0
        nonce = None
        serial = None
        if device_secret:
            from alarm.services.halo_recovery import factory_reset_with_verify

            result = factory_reset_with_verify(identity_name, device_secret,
                                               max_attempts=2)
            attempts = result.get("attempts", 0)
            if result.get("success"):
                factory_reset_confirmed = True
                nonce = result.get("nonce")
                serial = result.get("serial")
                logging.info(
                    "halo_offboard verified_success slug=%s nonce=%s serial=%s attempts=%d",
                    identity_name, nonce, serial, attempts,
                )
            else:
                result_reason = result.get("reason")
                nonce = result.get("last_nonce")
                serial = result.get("last_serial")
                logging.warning(
                    "halo_offboard verified_failed slug=%s reason=%s attempts=%d — proceeding with hub-side cleanup",
                    identity_name, result_reason, attempts,
                )
        else:
            logging.warning(
                "halo_offboard no_device_secret slug=%s — pre-v1.6 Halo. "
                "Skipping factory reset. Physical reset required.",
                identity_name,
            )

        # Hub-side cleanup runs unconditionally. Authoritative state.
        self.perform_destroy(instance)

        if factory_reset_confirmed:
            note = ("Halo factory reset and reverting to AP mode. "
                    "Re-scan the QR sticker to onboard fresh.")
        elif not device_secret:
            note = ("Hub-side cleanup complete. device_secret unknown — "
                    "physically reset (hold button) the Halo.")
        elif result_reason == "firmware_silent_no_pending":
            note = ("Halo did not respond to factory_reset (firmware MQTT "
                    "appears hung). Power-cycle the Halo and retry from the app.")
        elif result_reason and result_reason.startswith("denied:invalid_secret"):
            note = ("Halo rejected the stored secret — likely the device "
                    "was wiped out-of-band. Hub-side cleanup done. Power-"
                    "cycle the Halo to restart cleanly.")
        elif result_reason == "firmware_silent_no_confirmed":
            note = ("Halo accepted reset request but never confirmed the "
                    "wipe (firmware bug). Power-cycle the Halo and retry.")
        else:
            note = (f"Halo factory reset did not verify (reason: {result_reason}). "
                    "Hub-side cleanup done. Power-cycle the Halo and retry.")

        return Response(
            {
                "status": "success",
                "message": "Alarm device deleted from hub",
                "factory_reset_confirmed": factory_reset_confirmed,
                "factory_reset_attempts": attempts,
                "factory_reset_reason": result_reason,
                "factory_reset_nonce": nonce,
                "factory_reset_serial": serial,
                "note": note,
            },
            status=status.HTTP_200_OK,
        )

    def perform_destroy(self, instance):
        # Reset ESP32 in Home Assistant
        client = HassClient(
            hass_url=settings.HASS_URL,
            username=settings.HASS_USERNAME,
            password=settings.HASS_PASSWORD,
        )
        client.login()
        try:
            identity = instance.identity_name.replace("-", "_")
            client.call_service(
                "mqtt/publish",
                {
                    "topic": settings.HASS_MQTT_TOPIC_LISTEN_EVENT_TURN_OFF_AUTOMATION,
                    "payload": json.dumps({"label": "PERSON", "script": True}),
                },
            )

            time.sleep(0.5)

            for automation_id in [
                f"{identity}_ai_detected",
                f"{identity}_known_face_disarm",
                f"{identity}_manual_trigger",
                f"{identity}_alarm_stop_all",
                f"{identity}_smart_announcements",
                f"{identity}_voice_ai",
            ]:
                try:
                    client.delete_automation(automation_id)
                except Exception as exc:
                    logging.error(f"Failed to delete automation {automation_id}: {exc}")

            for script_id in [
                f"{identity}_ai_detected",
                f"{identity}_manual_trigger",
            ]:
                try:
                    client.delete_script(script_id)
                except Exception as exc:
                    logging.error(f"Failed to delete script {script_id}: {exc}")

        except Exception as exc:
            logging.warning(f"Failed to trigger turn off: {exc}")

        client.delete_device(instance.hass_entry_id)

        return super().perform_destroy(instance)


class RetrieveDeleteAlarmManualDeviceView(DestroyAPIView):
    model = AlarmDevice
    serializer_class = UpdateAlarmDeviceSerializer
    lookup_field = "id"
    queryset = AlarmDevice.objects.all()

    
    def perform_destroy(self, instance):
        # Reset ESP32 in Home Assistant
        client = HassClient(
            hass_url=settings.HASS_URL,
            username=settings.HASS_USERNAME,
            password=settings.HASS_PASSWORD,
        )
        client.login()
        try:
            identity = instance.identity_name.replace("-", "_")
            client.call_service(
                "mqtt/publish",
                {
                    "topic": settings.HASS_MQTT_TOPIC_LISTEN_EVENT_TURN_OFF_AUTOMATION,
                    "payload": json.dumps({"label": "PERSON", "script": True}),
                },
            )

            time.sleep(0.5)

            for automation_id in [
                f"{identity}_ai_detected",
                f"{identity}_known_face_disarm",
                f"{identity}_manual_trigger",
                f"{identity}_alarm_stop_all",
                f"{identity}_smart_announcements",
                f"{identity}_voice_ai",
            ]:
                try:
                    client.delete_automation(automation_id)
                except Exception as exc:
                    logging.error(f"Failed to delete automation {automation_id}: {exc}")

            for script_id in [
                f"{identity}_ai_detected",
                f"{identity}_manual_trigger",
            ]:
                try:
                    client.delete_script(script_id)
                except Exception as exc:
                    logging.error(f"Failed to delete script {script_id}: {exc}")

        except Exception as exc:
            logging.warning(f"Failed to trigger turn off: {exc}")

        client.delete_device(instance.hass_entry_id)

        return super().perform_destroy(instance)


class RebootAlarmDeviceView(APIView):
    def post(self, request, id, *args, **kwargs):
        device = get_object_or_404(AlarmDevice, id=id)
        device_name = device.identity_name

        try:
            publish_socket_message(
                {
                    "action": "restart",
                    "device_name": device_name,
                },
                wait_response=False,  # Don't wait - transfer_server doesn't relay responses
                timeout=5,
            )
            return Response({
                "status": "success",
                "message": "Restart command sent to device",
                "device": device_name,
            }, status=status.HTTP_200_OK)
        except Exception as exc:
            return Response({
                "status": "error",
                "message": f"Failed to send restart command: {exc}",
                "device": device_name,
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

   
class TurnOnOffAlarmView(GenericAPIView):
    serializer_class = TurnOnOffAlarmSerializer

    def post(self, request, *args, **kwargs):
        if not AlarmDevice.objects.exists():
            return Response(
                {
                    "status": "error",
                    "detail": "No alarm device configured",
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        state = request.data.get("state")
        sound = serializer.validated_data.get("sound")
        if state == "on":
            payload = json.dumps({"sound": sound})
            topic = settings.HASS_MQTT_TOPIC_CONTROL_MANUAL_ALARM_DEVICE
        else:
            topic = settings.HASS_MQTT_TOPIC_LISTEN_EVENT_TURN_OFF_AUTOMATION
            payload = json.dumps({})

        try:
            mqtt_client = MQTTClient(
                host=settings.MQTT_HOST,
                port=settings.MQTT_PORT,
                username=settings.MQTT_USERNAME,
                password=settings.MQTT_PASSWORD,
            )

            mqtt_client.connect()
            mqtt_client.publish(topic, payload)
            mqtt_client.close()
        except Exception as e:
            logging.exception("MQTT connect/publish failed")
            return Response(
                {
                    "status": "error",
                    "message": "Failed to send alarm command",
                    "detail": str(e),
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(
            {"status": "ok", "state": state, "sound": sound if state == "on" else None},
            status=status.HTTP_200_OK,
        )


class AlarmModeAPIView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = AlarmModeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        mode = serializer.validated_data["mode"]
        device = serializer.validated_data["device"]
        return Response(
            {
                "status": "ok",
                "mode": mode,
                "device": device,
            },
            status=status.HTTP_200_OK,
        )


class UpdateAlarmDeviceVersionFW(APIView):
    """Halo → hub firmware-version report.

    v1.6: accept BOTH legacy fleet-shared FRV_API_KEY (existing onboarded
    Halos in the field) AND new per-Halo HMAC token (from v1.6 onboard).
    Token check inline rather than via permission class so we have access
    to the request body's identity_name to derive the expected HMAC.
    """

    permission_classes = []  # auth is inline (token vs identity_name)

    def post(self, request):
        identity_name = request.data.get("identity_name")
        version_fw = request.data.get("version_fw")

        if not identity_name or not version_fw:
            return Response(
                {"detail": "identity_name and version_fw are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Accept the auth token in either header (preferred) or query params
        # for backwards compat. The Halo firmware sends it via api_path-derived
        # URL plus api_key on the URL/body — older clients sent api_key= as
        # request data; newer (v1.6) firmware can send it as
        # X-Halo-API-Token: <token>.
        presented_key = (
            request.headers.get("X-Halo-API-Token")
            or request.headers.get("X-API-KEY")
            or request.data.get("api_key")
            or request.query_params.get("api_key")
            or ""
        )

        from alarm.services.halo_token import verify_legacy_or_modern_key
        if not verify_legacy_or_modern_key(identity_name, presented_key):
            return Response(
                {"detail": "Invalid or missing Halo API token"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        device = get_object_or_404(AlarmDevice, identity_name=identity_name)
        device.version_fw = version_fw
        device.save(update_fields=["version_fw"])

        return Response(
            {
                "status": "ok",
                "identity_name": device.identity_name,
                "version_fw": device.version_fw,
            },
            status=status.HTTP_200_OK,
        )
