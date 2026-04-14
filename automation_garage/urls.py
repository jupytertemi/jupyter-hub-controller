from django.urls import path

from automation_garage.views import (
    GarageDoorSettingsByGarageView,
    GarageDoorSettingsView,
    UpdateGarageDoorSettingsView,
)

app_name = "automation"

urlpatterns = [
    path(
        "automations/garage-door-settings",
        GarageDoorSettingsView.as_view(),
        name="add-garage-door-settings",
    ),
    path(
        "automations/garage-door-settings/<int:pk>",
        UpdateGarageDoorSettingsView.as_view(),
        name="update-garage-door-settings",
    ),
    path(
        "automations/garage-door-settings/garage/<str:garage_id>",
        GarageDoorSettingsByGarageView.as_view(),
        name="get-garage-door-settings-by-garage-id",
    ),
]
