from django.urls import path

from hub_operations.views import OnboardingStatusView, ResettingProgressView, ResettingView, RestartCloudflaredView, RestartingView

app_name = "hub_operations"

urlpatterns = [
    path("restarting", RestartingView.as_view(), name="restarting_hub"),
    path("resetting", ResettingView.as_view(), name="resetting_hub"),
    path(
        "restarting/cloudflared", RestartCloudflaredView.as_view(), name="resetting_hub"
    ),
    path("resetting/progress", ResettingProgressView.as_view(), name="resetting_progress"),
    path("onboarding/status", OnboardingStatusView.as_view(), name="onboarding_status"),
    path("onboarding/progress", ResettingProgressView.as_view(), name="onboarding_progress"),
]
