from django.urls import path

from event.views import (
    EventVerdictView,
    ListCountEventView,
    ListEventView,
    RetrieveEventView,
)

app_name = "event"

urlpatterns = [
    path("events", ListEventView.as_view(), name="events"),
    path("events/counts", ListCountEventView.as_view(), name="counts"),
    path("events/<str:event_id>/verdict", EventVerdictView.as_view(), name="event_verdict"),
    path("events/<str:event_id>", RetrieveEventView.as_view(), name="event_detail"),
]
