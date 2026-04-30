from django.urls import path

from camera.views import (
    CameraRebootView,
    CameraRTSPSnapshotView,
    CameraSettingUpdateView,
    CameraSettingZoneView,
    CameraSnapshotProxyView,
    CameraVehicleCalibrationView,
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
    path("cameras/<str:slug>/snapshot", CameraSnapshotProxyView.as_view(), name="camera-snapshot"),
    path(
        "cameras/<str:slug>/rtsp-snapshot",
        CameraRTSPSnapshotView.as_view(),
        name="camera-rtsp-snapshot",
    ),
    path(
        "cameras/<str:slug>/vehicle-calibration",
        CameraVehicleCalibrationView.as_view(),
        name="camera-vehicle-calibration",
    ),
    path("cameras/<str:pk>/reboot", CameraRebootView.as_view(), name="camera-reboot"),
    path("cameras/<str:pk>", UpdateDeleteCameraView.as_view(), name="cameras"),
]
