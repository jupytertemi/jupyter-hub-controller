from django.urls import path

from .views import APNsTokenRegisterView, APNsTokenDeleteView, APNsTokenListView


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
]
