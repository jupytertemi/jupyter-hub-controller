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
    filterset_fields = ["type"]
    ordering_fields = ["created_at"]
    search_fields = ["name"]


class RetrieveDeleteAlarmDeviceView(RetrieveUpdateDestroyAPIView):
    model = AlarmDevice
    serializer_class = UpdateAlarmDeviceSerializer
    lookup_field = "id"
    queryset = AlarmDevice.objects.all()

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        identity_name = instance.identity_name
        
        # Try factory reset, but continue cleanup even if it fails
        factory_reset_success = False
        try:
            response = publish_socket_message(
                {
                    "action": "factory_reset",
                    "device_name": identity_name,
                },
                wait_response=False,  # Don't wait - transfer_server doesn't relay ESP responses
                timeout=5,
            )

            if response and response.get("status") == "ok" and response.get("device") == identity_name:
                factory_reset_success = True
                logging.info(f"Factory reset successful for {identity_name}")
            else:
                logging.warning(f"Factory reset failed for {identity_name}: {response}")
        except Exception as exc:
            logging.warning(f"Factory reset exception for {identity_name}: {exc}")

        # ALWAYS perform cleanup, regardless of factory reset result
        # User can manually reset device if needed
        self.perform_destroy(instance)
        
        return Response(
            {
                "status": "success",
                "message": "Alarm device deleted from hub",
                "factory_reset_sent": factory_reset_success,
                "note": "Factory reset sent. Device will reboot to setup mode." if factory_reset_success else "Failed to send reset command."
            },
            status=status.HTTP_200_OK
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
