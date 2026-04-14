from django.urls import path

from file.views import (
    CreatePlayBackSessionView,
    CreateTransferSessionView,
    FileUploadView,
)

app_name = "file"

urlpatterns = [
    path("files/transfer", CreateTransferSessionView.as_view(), name="files"),
    path("files/playback", CreatePlayBackSessionView.as_view(), name="playback-video"),
    path("files/upload", FileUploadView.as_view(), name="files-upload"),
]
