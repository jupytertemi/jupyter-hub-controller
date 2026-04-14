from rest_framework.exceptions import NotFound
from rest_framework.generics import RetrieveUpdateAPIView

from automation.models import AlarmSettings
from automation.serializers import AlarmSettingsSerializer


class AlarmSettingsView(RetrieveUpdateAPIView):
    model = AlarmSettings
    serializer_class = AlarmSettingsSerializer
    queryset = AlarmSettings.objects.all()
    lookup_field = "device_id"
    http_method_names = ["get", "put"]

    def get_object(self):
        device_id = self.kwargs["device_id"]
        try:
            return self.get_queryset().get(device_id=int(device_id))
        except (ValueError, TypeError):
            raise NotFound(detail="Invalid alarm ID.")
        except AlarmSettings.DoesNotExist:
            raise NotFound(detail="Alarm settings not found.")
