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
from alarm.views_halo_onboard import (
    HaloOnboardPayloadView,
    AlarmWaitOnlineView,
    HaloRegisterWebhookView,
    HaloRecoverySecretView,
)

app_name = "alarm"

urlpatterns = [
    # ---- Legacy / existing ----
    path("alarms", ListCreateAlarmDeviceView.as_view(), name="alarms"),
    path("alarms/mode", AlarmModeAPIView.as_view(), name="alarm-mode"),
    path("alarms/manual", TurnOnOffAlarmView.as_view()),
    path("alarms/<str:id>/reboot", RebootAlarmDeviceView.as_view()),
    path(
        "alarms/version-fw/update",
        UpdateAlarmDeviceVersionFW.as_view(),
        name="update-alarm-device-version-fv",
    ),
    path("alarms/<str:id>/manual", RetrieveDeleteAlarmManualDeviceView.as_view()),
    # ---- v1.6 Halo onboard ----
    path(
        "halo/onboard-payload",
        HaloOnboardPayloadView.as_view(),
        name="halo-onboard-payload",
    ),
    path(
        "alarms/wait-online",
        AlarmWaitOnlineView.as_view(),
        name="alarm-wait-online",
    ),
    path(
        "internal/halo-register",
        HaloRegisterWebhookView.as_view(),
        name="halo-register-webhook",
    ),
    path(
        "alarms/<str:slug>/recovery-secret",
        HaloRecoverySecretView.as_view(),
        name="halo-recovery-secret",
    ),
    # ---- Legacy detail (must be LAST — wildcard catch-all) ----
    path("alarms/<str:id>", RetrieveDeleteAlarmDeviceView.as_view()),
]
