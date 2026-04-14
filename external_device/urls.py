from django.urls import path

from external_device.views import (
    ClearExternalDeviceView,
    ListCreateExternalDeviceView,
    UpdateDeleteExternalDeviceView,
)

app_name = "external_device"

urlpatterns = [
    path(
        "external_device",
        ListCreateExternalDeviceView.as_view(),
    ),
    path(
        "external_device/clear",
        ClearExternalDeviceView.as_view(),
    ),
    path(
        "external_device/<str:id>",
        UpdateDeleteExternalDeviceView.as_view(),
    ),
]
