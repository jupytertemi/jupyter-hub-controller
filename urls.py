from django.urls import path

from hub_operations.views import ResettingView, RestartCloudflaredView, RestartingView

app_name = "hub_operations"

urlpatterns = [
    path("restarting", RestartingView.as_view(), name="restarting_hub"),
    path("resetting", ResettingView.as_view(), name="resetting_hub"),
    path(
        "restarting/cloudflared", RestartCloudflaredView.as_view(), name="resetting_hub"
    ),
]
