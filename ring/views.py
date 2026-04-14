from asgiref.sync import async_to_sync
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.generics import (
    CreateAPIView,
    DestroyAPIView,
    ListAPIView,
    RetrieveAPIView,
)
from rest_framework.response import Response

from core.pagination import Pagination
from ring.models import RingAccount
from ring.serializers import (
    RingAccountLoginSerializer,
    RingAccountSerializer,
    RingDeviceListSerializer,
)
from ring.tasks import set_ring_token


class RingAccountLoginView(CreateAPIView):
    model = RingAccount
    serializer_class = RingAccountLoginSerializer


class ListRingAccountView(ListAPIView):
    model = RingAccount
    serializer_class = RingAccountSerializer
    queryset = RingAccount.objects.all()
    pagination_class = Pagination
    filter_backends = [OrderingFilter, SearchFilter]
    ordering_fields = ["created_at"]
    search_fields = ["name"]


class DestroyRingAccountView(DestroyAPIView):
    model = RingAccount
    serializer_class = RingAccountSerializer
    lookup_field = "id"
    queryset = RingAccount.objects.all()

    def perform_destroy(self, instance):
        set_ring_token.apply_async(args=("",), queue="camera_queue")
        instance.delete()


class RingDeviceListView(RetrieveAPIView):
    model = RingAccount
    serializer_class = RingDeviceListSerializer
    lookup_field = "id"
    queryset = RingAccount.objects.all()

    def retrieve(self, request, *args, **kwargs):
        serializer = self.get_serializer(
            async_to_sync(RingAccount.objects.get_devices)(), many=True
        )
        return Response(serializer.data)
