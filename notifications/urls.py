from django.urls import path

from .views import (
    APNsTokenRegisterView,
    APNsTokenDeleteView,
    APNsTokenListView,
    TestApnsPushView,
)


urlpatterns = [
    path(
        "devices/apns-token",
        APNsTokenRegisterView.as_view(),
        name="apns-token-register",
    ),
    path(
        "devices/apns-token/<str:device_id>",
        APNsTokenDeleteView.as_view(),
        name="apns-token-delete",
    ),
    path(
        "devices/apns-tokens",
        APNsTokenListView.as_view(),
        name="apns-token-list",
    ),
    path(
        "system/test-apns-push",
        TestApnsPushView.as_view(),
        name="test-apns-push",
    ),
]
