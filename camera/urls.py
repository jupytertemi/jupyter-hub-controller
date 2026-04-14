from django.urls import path

from camera.views import (
    CameraSettingUpdateView,
    CameraSettingZoneView,
    ListCameraView,
    ListCreateRingCameraView,
    ListCreateRTSPCameraView,
    ListRTSPCameraURLView,
    RTSPDiscoverView,
    UpdateDeleteCameraView,
)

app_name = "camera"

urlpatterns = [
    path("cameras/rtsp/discover", RTSPDiscoverView.as_view(), name="cameras"),
    path("cameras/rtsp", ListCreateRTSPCameraView.as_view(), name="cameras"),
    path("cameras/rtsp/url", ListRTSPCameraURLView.as_view(), name="cameras"),
    path("cameras/ring", ListCreateRingCameraView.as_view(), name="cameras"),
    path("cameras", ListCameraView.as_view(), name="cameras"),
    path("cameras/setting", CameraSettingUpdateView.as_view(), name="cameras/setting"),
    path("cameras/zone", CameraSettingZoneView.as_view(), name="cameras"),
    path("cameras/<str:pk>", UpdateDeleteCameraView.as_view(), name="cameras"),
]
