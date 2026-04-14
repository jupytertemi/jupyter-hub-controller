from django.urls import path

from alarm.views import (
    AlarmModeAPIView,
    ListCreateAlarmDeviceView,
    RebootAlarmDeviceView,
    RetrieveDeleteAlarmDeviceView,
    RetrieveDeleteAlarmManualDeviceView,
    TurnOnOffAlarmView,
    UpdateAlarmDeviceVersionFW,
)

app_name = "alarm"

urlpatterns = [
    path("alarms", ListCreateAlarmDeviceView.as_view(), name="alarms"),
    path("alarms/mode", AlarmModeAPIView.as_view(), name="alarm-mode"),
    path(
        "alarms/manual",
        TurnOnOffAlarmView.as_view(),
    ),
    path(
        "alarms/<str:id>/reboot",
        RebootAlarmDeviceView.as_view(),
    ),
    path(
        "alarms/version-fw/update",
        UpdateAlarmDeviceVersionFW.as_view(),
        name="update-alarm-device-version-fv",
    ),
    path(
        "alarms/<str:id>/manual",
        RetrieveDeleteAlarmManualDeviceView.as_view(),
    ),
    path(
        "alarms/<str:id>",
        RetrieveDeleteAlarmDeviceView.as_view(),
    ),
]
