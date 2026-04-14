from django.urls import path

from facial.views import DeleteFacialView, ListCreateFacialView, serve_face_avatar

app_name = "facial"

urlpatterns = [
    path("facial", ListCreateFacialView.as_view(), name="facial"),
    path(
        "facial/<str:id>",
        DeleteFacialView.as_view(),
    ),
    path("face-avatar/<str:filename>", serve_face_avatar, name="face-avatar"),
]
