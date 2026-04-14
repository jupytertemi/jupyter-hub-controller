from django.urls import path

from loitering.views import LoiteringView

app_name = "loitering"

urlpatterns = [
    path("loitering", LoiteringView.as_view(), name="loitering"),
]
