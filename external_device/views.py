from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.generics import (
    GenericAPIView,
    ListCreateAPIView,
    RetrieveUpdateDestroyAPIView,
)
from rest_framework.response import Response

from core.pagination import Pagination
from external_device.enum import ExternalDeviceStatus
from external_device.models import ExternalDevice
from external_device.serializers import (
    ExternalDeviceClearSerializer,
    ExternalDeviceSerializer,
)
from utils.socket_publisher import publish_socket_message


class ListCreateExternalDeviceView(ListCreateAPIView):
    model = ExternalDevice
    serializer_class = ExternalDeviceSerializer
    queryset = ExternalDevice.objects.all()
    pagination_class = Pagination
    filter_backends = [DjangoFilterBackend, OrderingFilter, SearchFilter]
    filterset_fields = ["type"]
    ordering_fields = ["created_at"]
    search_fields = ["id"]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        response = publish_socket_message(
            {
                "action": "add",
                "type": serializer.validated_data["type"],
                "mac": serializer.validated_data["mac_address"],
            },
            wait_response=True,
        )

        if not response:
            return Response(
                {
                    "status": "error",
                    "action": "add",
                    "result": "failed",
                    "message": "Timeout waiting websocket response",
                    "mac": serializer.validated_data["mac_address"],
                },
                status=status.HTTP_504_GATEWAY_TIMEOUT,
            )

        if response.get("result") != "success":
            return Response(response, status=status.HTTP_400_BAD_REQUEST)

        instance = serializer.save(
            status=ExternalDeviceStatus.SUCCESS,
            socket_response=response,
        )
        output = self.get_serializer(instance)
        headers = self.get_success_headers(output.data)
        return Response(output.data, status=status.HTTP_201_CREATED, headers=headers)


class UpdateDeleteExternalDeviceView(RetrieveUpdateDestroyAPIView):
    model = ExternalDevice
    serializer_class = ExternalDeviceSerializer
    lookup_field = "id"
    queryset = ExternalDevice.objects.all()


class ClearExternalDeviceView(GenericAPIView):
    model = ExternalDevice
    serializer_class = ExternalDeviceClearSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        device_type = serializer.validated_data["type"]

        if not device_type:
            return Response(
                {"detail": "type is required"}, status=status.HTTP_400_BAD_REQUEST
            )
        ExternalDevice.objects.filter(type=device_type).delete()

        publish_socket_message(
            {
                "action": "clear",
                "type": device_type,
            }
        )

        return Response({"status": "cleared"}, status=status.HTTP_200_OK)
