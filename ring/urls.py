from django.urls import path

from ring.views import (
    DestroyRingAccountView,
    ListRingAccountView,
    RingAccountLoginView,
    RingDeviceListView,
)

app_name = "ring"

urlpatterns = [
    path("ring/login", RingAccountLoginView.as_view(), name="ring"),
    path("ring/accounts", ListRingAccountView.as_view(), name="ring"),
    path("ring/accounts/<str:id>", DestroyRingAccountView.as_view(), name="ring"),
    path("ring/accounts/<str:id>/devices", RingDeviceListView.as_view(), name="ring"),
]
