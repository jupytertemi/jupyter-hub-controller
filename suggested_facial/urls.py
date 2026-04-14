from django.urls import path

from suggested_facial.views import ListSuggestedFacialView, UpdateSuggestedFacialView

app_name = "suggested_facial"

urlpatterns = [
    path(
        "suggested_facial", ListSuggestedFacialView.as_view(), name="suggested_facial"
    ),
    path(
        "suggested_facial/<int:pk>",
        UpdateSuggestedFacialView.as_view(),
        name="update_suggested_facial",
    ),
]
