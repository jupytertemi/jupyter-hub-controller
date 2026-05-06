from django.urls import path

from meross.views import (
    AddMerossCloudView,
    DestroyMerossCloudAccountView,
    GetDeviceEntityIdsView,
    GetStatesEntityView,
    ListCreateMerossDeviceView,
    ListMerossDeviceDiscoveryView,
    MerossControlView,
    SendMessagesWebSocketView,
    TurnOnOffMerossManualView,
    UpdateDestroyMerossDeviceView,
)

app_name = "meross"

urlpatterns = [
    path("meross", ListCreateMerossDeviceView.as_view(), name="meross-device"),
    path(
        "meross/discovery",
        ListMerossDeviceDiscoveryView.as_view(),
        name="meross-discovery",
    ),
    path(
        "meross/entity/<str:hass_entry_id>",
        GetDeviceEntityIdsView.as_view(),
        name="meross-device-id",
    ),
    path(
        "meross/states/<str:entity_id>",
        GetStatesEntityView.as_view(),
        name="meross-states",
    ),
    path(
        "meross/send_message_web_socket",
        SendMessagesWebSocketView.as_view(),
    ),
    path(
        "meross/cloud-account",
        AddMerossCloudView.as_view(),
        name="meross-cloud-account",
    ),
    path(
        "meross/manual",
        TurnOnOffMerossManualView.as_view(),
    ),
    path(
        "meross/<str:id>/control",
        MerossControlView.as_view(),
        name="meross-control",
    ),
    path(
        "meross/<str:id>", UpdateDestroyMerossDeviceView.as_view(), name="meross-device"
    ),
    path(
        "meross/cloud-account/<str:id>",
        DestroyMerossCloudAccountView.as_view(),
        name="meross-cloud-account",
    ),
]
