import logging

from rest_framework.exceptions import ValidationError
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.generics import DestroyAPIView, ListCreateAPIView, UpdateAPIView
from rest_framework.response import Response

from core.pagination import Pagination
from meross.models import MerossCloudAccount, MerossDevice
from meross.serializers import (
    GetStatesEntitySerializer,
    MerossCloudAccountSerializer,
    MerossCloudSettingSerializer,
    MerossDeviceSerializer,
    SendMessagesWebSocketSerializer,
    UpdateMerossDeviceSerializer,
)
from utils.hass_client import InterfaceHASSView


class ListCreateMerossDeviceView(ListCreateAPIView):
    model = MerossDevice
    serializer_class = MerossDeviceSerializer
    queryset = MerossDevice.objects.all()
    pagination_class = Pagination
    filter_backends = [OrderingFilter, SearchFilter]
    ordering_fields = ["created_at"]
    search_fields = ["name"]


class ListMerossDeviceDiscoveryView(InterfaceHASSView):
    serializer_class = MerossDeviceSerializer

    def get(self, request, *args, **kwargs):
        if not MerossCloudAccount.objects.exists():
            # Meross discovery is available only after adding a cloud account.
            return Response(data=[], status=200)

        client = self.getHassClient()

        try:
            entry = client.get_meross_device_discovered()
            return Response(data=entry, status=200)
        except Exception as err:
            raise ValidationError({"error": err})


class SendMessagesWebSocketView(InterfaceHASSView):
    serializer_class = SendMessagesWebSocketSerializer

    def post(self, request, *args, **kwargs):
        client = self.getHassClient()

        try:
            message = request.data.get("message")
            entry = client.send_message(message)
            return Response(data=entry, status=200)
        except Exception as err:
            raise ValidationError({"error": err})


class AddMerossCloudView(ListCreateAPIView):
    model = MerossCloudAccount
    serializer_class = MerossCloudSettingSerializer
    queryset = MerossCloudAccount.objects.all()


class DestroyEntryView(InterfaceHASSView, DestroyAPIView):
    model = None
    serializer_class = None
    lookup_field = "id"
    queryset = None

    def perform_destroy(self, instance):
        client = self.getHassClient()
        client.delete_device(instance.hass_entry_id)

        meross_account = MerossCloudAccount.objects.first()

        if meross_account:
            client.delete_device(meross_account.hass_entry_id)
            meross_account.delete()

        return super().perform_destroy(instance)


class UpdateDestroyMerossDeviceView(DestroyEntryView, UpdateAPIView):
    model = MerossDevice
    serializer_class = UpdateMerossDeviceSerializer
    queryset = MerossDevice.objects.all()


class DestroyMerossCloudAccountView(DestroyEntryView):
    model = MerossCloudAccount
    serializer_class = MerossCloudAccountSerializer
    queryset = MerossCloudAccount.objects.all()


class GetDeviceEntityIdsView(InterfaceHASSView):
    serializer_class = MerossCloudAccountSerializer

    def get(self, request, hass_entry_id, *args, **kwargs):
        client = self.getHassClient()

        try:
            entry = client.get_entities(hass_entry_id)
            return Response(data=entry, status=200)
        except Exception as err:
            raise ValidationError({"error": err})


class GetStatesEntityView(InterfaceHASSView):
    serializer_class = GetStatesEntitySerializer

    def get(self, request, entity_id, *args, **kwargs):
        client = self.getHassClient()

        try:
            entry = client.get_states_entity(entity_id)
            return Response(data=entry, status=200)
        except Exception as err:
            raise ValidationError({"error": err})

    def post(self, request, entity_id, *args, **kwargs):
        client = self.getHassClient()
        try:
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            resp = client.get_states_entity(entity_id)
            if resp.get("state") != "unavailable":
                resp["state"] = serializer.data.get("states")
                entry = client.control_states_entity(entity_id, resp)
                return Response(data=entry, status=200)
            else:
                return Response(
                    {"error": "Device is unavailable, cannot be controlled"}, status=503
                )
        except Exception as err:
            raise ValidationError({"error": err})


class TurnOnOffMerossManualView(InterfaceHASSView):
    serializer_class = GetStatesEntitySerializer

    def post(self, request, *args, **kwargs):
        client = self.getHassClient()
        try:
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            instant = MerossDevice.objects.first()
            if instant is None:
                return Response(
                    {"error": "Device is unavailable, cannot be controlled"}, status=404
                )
            entities = client.get_entities(instant.hass_entry_id)
            data = entities["result"]

            cover = next(
                (e for e in data.get("entity", []) if e.startswith("cover.")), None
            )
            resp = client.get_states_entity(cover)
            if resp.get("state") != "unavailable":
                resp["state"] = serializer.data.get("states")
                entry = client.control_states_entity(cover, resp)
                return Response(data=entry, status=200)
            else:
                return Response(
                    {"error": "Device is unavailable, cannot be controlled"}, status=503
                )
        except Exception as err:
            logging.error(err)
            raise ValidationError({"error": err})
