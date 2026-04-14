import json
import os
import urllib.request

from django.http import HttpResponse
from django_filters.rest_framework import DjangoFilterBackend
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.generics import (
    DestroyAPIView,
    ListAPIView,
    ListCreateAPIView,
    RetrieveUpdateAPIView,
    UpdateAPIView,
)
from rest_framework.response import Response
from rest_framework.views import APIView

from automation_garage.models import GarageDoorSettings
from camera.enums import CameraType
from camera.models import (  # CameraSetting,
    Camera,
    CameraSetting,
    CameraSettingZone,
    RingCamera,
    RTSPCamera,
)
from camera.serializers import (
    AddCameraSettingZoneSerializer,
    CameraSerializer,
    CameraSettingSerializer,
    CameraSettingZoneSerializer,
    RingCameraSerializer,
    RTSPCameraSerializer,
    RTSPCameraUrlSerializer,
    RTSPDiscoveringSerializer,
    UpdateCameraSerializer,
)
from camera.tasks import update_camera_config, update_frigate_config, cleanup_ring_device
from ring.tasks import clear_ring_auth
from utils.restarting_service import restart_service
from core.pagination import Pagination


class RTSPDiscoverView(APIView):
    def get(self, request):
        discovered_camera = RTSPCamera.objects.discover()
        serializer = RTSPDiscoveringSerializer(discovered_camera, many=True)
        return Response(serializer.data)


class ListCreateRTSPCameraView(ListCreateAPIView):
    model = RTSPCamera
    serializer_class = RTSPCameraSerializer
    queryset = RTSPCamera.objects.all()
    pagination_class = Pagination
    filter_backends = [DjangoFilterBackend, OrderingFilter, SearchFilter]
    filterset_fields = ["name"]
    ordering_fields = ["created_at"]
    search_fields = ["name"]


class ListRTSPCameraURLView(APIView):

    @swagger_auto_schema(
        query_serializer=RTSPCameraUrlSerializer(),
    )
    def get(self, request, *args, **kwargs):
        query = self.request.GET.copy()
        rtsp_url = RTSPCamera.objects.get_rtsp_url(query)
        data = {
            "rtsp_url": rtsp_url,
            "message": "Get camera rtsp url successfully.",
        }
        # Include ONVIF device info so Flutter can pre-fill manufacturer/model search
        device_info = RTSPCamera.objects.get_onvif_device_info(
            query.get("ip", ""),
            query.get("username", ""),
            query.get("password", ""),
        )
        if device_info:
            data["onvif_manufacturer"] = device_info["manufacturer"]
            data["onvif_model"] = device_info["model"]
        return Response(data)


class ListCreateRingCameraView(ListCreateAPIView):
    model = RingCamera
    serializer_class = RingCameraSerializer
    queryset = RingCamera.objects.all()
    pagination_class = Pagination
    filter_backends = [OrderingFilter, SearchFilter]
    ordering_fields = ["created_at"]
    search_fields = ["name"]


class ListCameraView(ListAPIView):
    model = Camera
    serializer_class = CameraSerializer
    queryset = Camera.objects.all()
    pagination_class = Pagination
    filter_backends = [OrderingFilter, SearchFilter]
    ordering_fields = ["created_at"]
    search_fields = ["name"]


class UpdateDeleteCameraView(UpdateAPIView, DestroyAPIView):
    model = Camera
    serializer_class = UpdateCameraSerializer
    lookup_field = "pk"
    queryset = Camera.objects.all()

    def perform_update(self, serializer):
        serializer.save()
        instance = self.get_object()
        garage_setting = GarageDoorSettings.objects.filter(camera=instance)
        if garage_setting:
            setting = garage_setting.first()
            GarageDoorSettings.objects.create_hass_automation(
                **{
                    "garage": setting.garage,
                    "camera": setting.camera,
                    "active_open": setting.active_open,
                    "auto_close": setting.auto_close,
                    "auto_close_delay": setting.auto_close_delay,
                    "auto_open_on_owner": setting.auto_open_on_owner,
                    "card_on_owner": setting.card_on_owner,
                    "card_on_unknown": setting.card_on_unknown,
                }
            )

    def perform_destroy(self, instance):
        camera = CameraSetting.objects.first()
        update_file = {}

        if camera and camera.parcel_detect_camera == instance:
            update_file["parcel_detect_camera"] = None
        if camera and camera.loitering_camera == instance:
            update_file["loitering_camera"] = None
        update_frigate = True
        if Camera.objects.count() == 1:
            update_file.update(
                {
                    "enable_parcel_detect": False,
                    "parcel_detect_camera": None,
                    "enable_face_recognition": False,
                    "loitering_recognition": False,
                    "loitering_camera": None,
                    "license_vehicle_recognition": False,
                    "activate_sounds_detection": False,
                    "footage_retention_period": False,
                }
            )
        if update_file:
            serializer = CameraSettingSerializer(
                instance, data=update_file, partial=True
            )
            serializer.is_valid(raise_exception=True)
            CameraSetting.objects.update(instance, serializer.validated_data)

        garage_setting = GarageDoorSettings.objects.filter(camera=instance)
        if garage_setting:
            setting = garage_setting.first()
            GarageDoorSettings.objects.create_hass_automation(
                **{
                    "garage": setting.garage,
                    "camera": None,
                    "active_open": setting.active_open,
                    "auto_close": setting.auto_close,
                    "auto_close_delay": setting.auto_close_delay,
                    "auto_open_on_owner": setting.auto_open_on_owner,
                    "card_on_owner": setting.card_on_owner,
                    "card_on_unknown": setting.card_on_unknown,
                }
            )
        # Clean up cached thumbnail
        slug = getattr(instance, "slug_name", None)
        if slug:
            thumb = os.path.join(CameraSnapshotProxyView.THUMBNAIL_DIR, f"{slug}.jpg")
            if os.path.exists(thumb):
                os.remove(thumb)

        # Capture camera type and Ring device ID before deletion
        is_ring_camera = instance.type == CameraType.RING
        ring_device_id = getattr(instance, 'ring_device_id', None)

        instance.delete()

        if RingCamera.objects.count() == 0:
            RingCamera.objects.delete_restart_ring_task()
            if is_ring_camera:
                # Clear orphaned Ring auth (DB + state files) so a new
                # Ring doorbell can be onboarded without conflicts.
                clear_ring_auth.apply_async(queue="camera_queue")
                try:
                    from django.conf import settings
                    restart_service(settings.RING_STREAM_CONTAINER)
                except Exception:
                    pass

        if RTSPCamera.objects.count() == 0:
            RTSPCamera.objects.delete_ip_monitor_task()

        if update_frigate:
            update_camera_config.delay()

        # Purge Ring device traces from ring-state.json, HA, MQTT
        if is_ring_camera and ring_device_id:
            cleanup_ring_device.delay(ring_device_id)


class CameraSettingUpdateView(RetrieveUpdateAPIView):
    model = CameraSetting
    queryset = CameraSetting.objects.all()
    serializer_class = CameraSettingSerializer
    http_method_names = ["get", "patch"]

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_queryset().first()
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    def patch(self, request, *args, **kwargs):
        instance = self.get_queryset().first()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        # Delegate update logic to the manager
        updated_instance = self.model.objects.update(
            instance, serializer.validated_data
        )
        return Response(
            self.get_serializer(updated_instance).data, status=status.HTTP_200_OK
        )


class CameraSettingZoneView(ListCreateAPIView):
    model = CameraSettingZone
    queryset = CameraSettingZone.objects.all()
    pagination_class = Pagination
    serializer_class = CameraSettingZoneSerializer
    filter_backends = [DjangoFilterBackend, OrderingFilter, SearchFilter]
    filterset_fields = ["camera"]

    def get_serializer_class(self):
        if self.request.method == "POST":
            return AddCameraSettingZoneSerializer
        return CameraSettingZoneSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        zones = serializer.save()
        update_frigate_config.delay()
        return Response(
            {
                "zones": CameraSettingZoneSerializer(zones, many=True).data,
            },
            status=status.HTTP_201_CREATED,
        )


class CameraSnapshotProxyView(APIView):
    """Proxy for camera snapshots with smart Frigate polling.

    Checks Frigate stats to see if the camera is actively streaming before
    fetching a live frame.  When Frigate is live, the cached thumbnail is
    updated so subsequent requests (even if Frigate goes down again) always
    have the freshest image.  When Frigate is not ready, the cached
    thumbnail captured during onboarding is served instantly.
    """
    permission_classes = []
    authentication_classes = []

    THUMBNAIL_DIR = "/root/jupyter-hub-controller/media/thumbnails"

    def _frigate_camera_active(self, slug):
        """Return True if Frigate reports camera_fps > 0 for *slug*."""
        try:
            req = urllib.request.Request("http://127.0.0.1:5000/api/stats")
            with urllib.request.urlopen(req, timeout=2) as resp:
                stats = json.loads(resp.read())
                return stats.get("cameras", {}).get(slug, {}).get(
                    "camera_fps", 0
                ) > 0
        except Exception:
            return False

    def _fetch_frigate_frame(self, slug):
        """Fetch latest JPEG from Frigate. Returns bytes or None."""
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:5000/api/{slug}/latest.jpg"
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                content = resp.read()
                if len(content) > 100:
                    return content
        except Exception:
            pass
        return None

    def get(self, request, slug):
        # If Frigate is actively streaming, serve live frame and update cache
        if self._frigate_camera_active(slug):
            frame = self._fetch_frigate_frame(slug)
            if frame:
                # Update cached thumbnail with fresh frame
                try:
                    os.makedirs(self.THUMBNAIL_DIR, exist_ok=True)
                    path = os.path.join(self.THUMBNAIL_DIR, f"{slug}.jpg")
                    with open(path, "wb") as f:
                        f.write(frame)
                except Exception:
                    pass
                return HttpResponse(
                    frame,
                    content_type="image/jpeg",
                    headers={"X-Snapshot-Source": "frigate-live"},
                )

        # Frigate not active — serve cached thumbnail
        path = os.path.join(self.THUMBNAIL_DIR, f"{slug}.jpg")
        if os.path.exists(path):
            with open(path, "rb") as f:
                return HttpResponse(
                    f.read(),
                    content_type="image/jpeg",
                    headers={"X-Snapshot-Source": "cached-thumbnail"},
                )

        return HttpResponse(status=404)
