from django.urls import path

from automation.views import AlarmSettingsView

app_name = "automation"

urlpatterns = [
    path(
        "alarms/<str:device_id>/automations",
        AlarmSettingsView.as_view(),
        name="automations",
    )
]
