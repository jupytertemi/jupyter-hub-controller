from django.urls import path

from logbook.views import GetLogbookEntityView, GetLogbookView

app_name = "logbook"

urlpatterns = [
    path("logbook", GetLogbookView.as_view(), name="logbooks"),
    path(
        "logbook/<str:entity_id>",
        GetLogbookEntityView.as_view(),
        name="logbook",
    ),
]
