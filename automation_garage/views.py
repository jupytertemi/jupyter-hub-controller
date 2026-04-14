from rest_framework.exceptions import NotFound
from rest_framework.generics import (
    CreateAPIView,
    RetrieveAPIView,
    RetrieveUpdateAPIView,
)

from automation_garage.models import GarageDoorSettings
from automation_garage.serializers import GarageDoorSettingsSerializer


class GarageDoorSettingsView(CreateAPIView):
    model = GarageDoorSettings
    serializer_class = GarageDoorSettingsSerializer
    queryset = GarageDoorSettings.objects.all()


class UpdateGarageDoorSettingsView(RetrieveUpdateAPIView):
    model = GarageDoorSettings
    serializer_class = GarageDoorSettingsSerializer
    queryset = GarageDoorSettings.objects.all()
    http_method_names = ["get", "put"]


class GarageDoorSettingsByGarageView(RetrieveAPIView):
    model = GarageDoorSettings
    serializer_class = GarageDoorSettingsSerializer
    queryset = GarageDoorSettings.objects.all()
    lookup_field = "garage_id"

    def get_object(self):
        garage_id = self.kwargs["garage_id"]
        try:
            return self.get_queryset().get(garage_id=int(garage_id))
        except (ValueError, TypeError):
            raise NotFound(detail="Invalid garage ID.")
        except GarageDoorSettings.DoesNotExist:
            raise NotFound(detail="Garage not found.")
