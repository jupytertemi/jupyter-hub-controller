from rest_framework.exceptions import NotFound
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.generics import (
    ListCreateAPIView,
    RetrieveAPIView,
    RetrieveUpdateDestroyAPIView,
)

from core.pagination import Pagination
from vehicle.models import Vehicle
from vehicle.serializers import VehicleSerializer


class ListVehicleView(ListCreateAPIView):
    model = Vehicle
    serializer_class = VehicleSerializer
    queryset = Vehicle.objects.all()
    pagination_class = Pagination
    filter_backends = [OrderingFilter, SearchFilter]
    ordering_fields = ["start_time"]
    search_fields = ["id"]


class UpdateDeleteVehicleView(RetrieveUpdateDestroyAPIView):
    model = Vehicle
    serializer_class = VehicleSerializer
    lookup_field = "id"
    queryset = Vehicle.objects.all()


class RetrieveVehicleView(RetrieveAPIView):
    model = Vehicle
    serializer_class = VehicleSerializer
    queryset = Vehicle.objects.all()
    lookup_field = "license_plate"

    def get_object(self):
        license_plate = self.kwargs["license_plate"]
        try:
            return self.get_queryset().get(license_plate=license_plate)
        except (ValueError, TypeError):
            raise NotFound(detail="Invalid Event ID.")
        except Vehicle.DoesNotExist:
            raise NotFound(detail="Event not found.")
