from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r"schedule", views.BackupScheduleViewSet, basename="backup-schedule")

urlpatterns = [
    # OAuth flow
    path("auth/url", views.OAuthURLView.as_view(), name="gdrive-auth-url"),
    path("auth/callback", views.OAuthCallbackView.as_view(), name="gdrive-auth-callback"),
    path("auth/device-start", views.DeviceCodeStartView.as_view(), name="gdrive-device-start"),
    path("auth/device-poll", views.DeviceCodePollView.as_view(), name="gdrive-device-poll"),
    path("auth/status", views.OAuthStatusView.as_view(), name="gdrive-auth-status"),
    path("auth/disconnect", views.OAuthDisconnectView.as_view(), name="gdrive-auth-disconnect"),

    # Backup operations
    path("backup/start", views.BackupStartView.as_view(), name="gdrive-backup-start"),
    path("backup/list", views.BackupListView.as_view(), name="gdrive-backup-list"),
    path("backup/<int:pk>", views.BackupDetailView.as_view(), name="gdrive-backup-detail"),
    path("backup/<int:pk>/cancel", views.BackupCancelView.as_view(), name="gdrive-backup-cancel"),
    path("backup/<int:pk>/delete", views.BackupDeleteView.as_view(), name="gdrive-backup-delete"),
    path("backup/<int:pk>/restore", views.BackupRestoreView.as_view(), name="gdrive-backup-restore"),

    # Space management
    path("space", views.DriveSpaceView.as_view(), name="gdrive-space"),

    # Test page (browser-based testing)
    path("test", views.TestPageView.as_view(), name="gdrive-test"),

    # Scheduling (router-based)
    path("", include(router.urls)),
]
