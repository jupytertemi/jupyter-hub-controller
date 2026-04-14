from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.generics import ListAPIView

from core.pagination import CustomCursorPagination
from manufacturer.filter import CameraModelFilter
from manufacturer.models import CameraManufacturer, CameraModel
from manufacturer.serializers import CameraManufacturerSerializer, CameraModelSerializer


class ListCameraManufacturerView(ListAPIView):
    model = CameraManufacturer
    serializer_class = CameraManufacturerSerializer
    queryset = CameraManufacturer.objects.all()
    pagination_class = CustomCursorPagination
    filter_backends = [OrderingFilter, SearchFilter]
    ordering_fields = ["manufacturer_name", "id"]
    search_fields = ["manufacturer_name"]


class ListCameraModelView(ListAPIView):
    model = CameraModel
    serializer_class = CameraModelSerializer
    queryset = CameraModel.objects.all()
    pagination_class = CustomCursorPagination
    filter_backends = [DjangoFilterBackend, OrderingFilter, SearchFilter]
    filterset_class = CameraModelFilter
    ordering_fields = ["id", "model", "type"]
    search_fields = ["model", "type"]
