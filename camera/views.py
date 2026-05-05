import json
import logging
import os
import subprocess
import tempfile
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
    VehicleCalibrationSerializer,
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
        query = self.request.GET.dict()
        result = RTSPCamera.objects.get_rtsp_url(query)
        if isinstance(result, dict):
            return Response(result)
        return Response(
            {"rtsp_url": result, "message": "Get camera rtsp url successfully."}
        )


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
    """Legacy snapshot endpoint, used by WebRTCPlayer poster + dashboard tiles.

    Was Frigate-first only; if Frigate had no recent frame and no cached
    thumbnail existed, it returned 404 → "Camera snapshot not available yet".

    2026-05-01 — extended to 3-tier fallback so the snapshot is reliable
    everywhere in the app, not just the calibration wizard:

      1. Frigate latest.jpg          fast path, ~100ms when active
      2. Direct RTSP via ffmpeg      3s timeout, only when Frigate is dead
      3. Cached thumbnail            instant, captured at last successful
                                     Frigate or RTSP fetch

    Frigate is tried first to preserve the snappy dashboard / WebRTC poster
    behaviour. RTSP only kicks in when Frigate is genuinely unavailable
    (idle camera, Frigate restarting, no fps detected).
    """
    permission_classes = []
    authentication_classes = []

    # Shared with camera.tasks.SNAPSHOT_DIR via settings — see CAMERA_THUMBNAILS_DIR
    # in hub_controller/settings/common.py. Single source of truth.
    THUMBNAIL_DIR = getattr(
        __import__("django.conf", fromlist=["settings"]).settings,
        "CAMERA_THUMBNAILS_DIR",
        "/root/jupyter-hub-controller/media/thumbnails",
    )
    FRIGATE_TIMEOUT_S = 2
    RTSP_TIMEOUT_S = 3

    def _fetch_frigate_frame(self, slug):
        """Fetch latest JPEG from Frigate (works whether camera_fps>0 or not).
        Returns bytes or None. We attempt the fetch unconditionally — if
        Frigate is up at all and has any frame for this slug it returns it.
        """
        try:
            url = f"http://127.0.0.1:5000/api/{slug}/latest.jpg"
            with urllib.request.urlopen(url, timeout=self.FRIGATE_TIMEOUT_S) as resp:
                content = resp.read()
                if len(content) > 100:
                    return content
                _snapshot_log.warning("[%s] frigate returned tiny payload (%d bytes)", slug, len(content))
        except Exception as exc:
            _snapshot_log.warning("[%s] frigate fetch failed: %s", slug, exc)
        return None

    def _capture_rtsp(self, rtsp_url, slug):
        """RTSP fallback for when Frigate is dead. Returns bytes or None."""
        if not rtsp_url:
            return None
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            out_path = f.name
        try:
            cmd = [
                "ffmpeg", "-y",
                "-hide_banner", "-loglevel", "error",
                "-rtsp_transport", "tcp",
                "-timeout", str(self.RTSP_TIMEOUT_S * 1_000_000),
                "-i", rtsp_url,
                "-frames:v", "1",
                "-q:v", "2",
                out_path,
            ]
            proc = subprocess.run(cmd, capture_output=True, timeout=self.RTSP_TIMEOUT_S + 1)
            if proc.returncode != 0:
                err_tail = (proc.stderr or b"").decode("utf-8", errors="replace").strip().splitlines()
                _snapshot_log.warning(
                    "[%s] ffmpeg rc=%d: %s",
                    slug, proc.returncode,
                    err_tail[-1] if err_tail else "no stderr",
                )
                return None
            if os.path.getsize(out_path) < 100:
                return None
            with open(out_path, "rb") as f:
                return f.read()
        except subprocess.TimeoutExpired:
            _snapshot_log.warning("[%s] ffmpeg wall-clock timeout", slug)
            return None
        except Exception as exc:
            _snapshot_log.exception("[%s] ffmpeg exception: %s", slug, exc)
            return None
        finally:
            try:
                os.unlink(out_path)
            except OSError:
                pass

    def _save_cache(self, slug, frame):
        try:
            os.makedirs(self.THUMBNAIL_DIR, exist_ok=True)
            with open(os.path.join(self.THUMBNAIL_DIR, f"{slug}.jpg"), "wb") as f:
                f.write(frame)
        except OSError:
            pass

    def get(self, request, slug):
        # 1. Frigate first (fast path)
        frame = self._fetch_frigate_frame(slug)
        if frame:
            self._save_cache(slug, frame)
            return HttpResponse(
                frame,
                content_type="image/jpeg",
                headers={"X-Snapshot-Source": "frigate-live", "Cache-Control": "no-store"},
            )

        # 2. RTSP fallback (only if Frigate's dead — adds 3s but saves the user)
        try:
            camera = Camera.objects.get(slug_name=slug)
        except Camera.DoesNotExist:
            return HttpResponse(status=404)

        if camera.type == CameraType.RTSP and camera.rtsp_url:
            frame = self._capture_rtsp(camera.rtsp_url, slug)
            if frame:
                self._save_cache(slug, frame)
                return HttpResponse(
                    frame,
                    content_type="image/jpeg",
                    headers={"X-Snapshot-Source": "rtsp-fallback", "Cache-Control": "no-store"},
                )

        # 3. Cached thumbnail
        path = os.path.join(self.THUMBNAIL_DIR, f"{slug}.jpg")
        if os.path.exists(path):
            try:
                with open(path, "rb") as f:
                    return HttpResponse(
                        f.read(),
                        content_type="image/jpeg",
                        headers={"X-Snapshot-Source": "cached-thumbnail", "Cache-Control": "no-store"},
                    )
            except OSError:
                pass

        _snapshot_log.error("[%s] all three snapshot sources dead", slug)
        return HttpResponse(status=404)


class CameraRebootView(APIView):
    @staticmethod
    def _extract_rtsp_credentials(rtsp_url):
        """Extract username/password from rtsp://user:pass@host/... URL."""
        if not rtsp_url:
            return "", ""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(rtsp_url)
            return parsed.username or "", parsed.password or ""
        except Exception:
            return "", ""

    def post(self, request, pk):
        try:
            camera = Camera.objects.get(pk=pk)
        except Camera.DoesNotExist:
            return Response(
                {"error": "Camera not found"}, status=status.HTTP_404_NOT_FOUND
            )

        if not camera.ip:
            return Response(
                {"error": "Camera IP not available"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            from onvif import ONVIFCamera, ONVIFError

            username = camera.username or ""
            password = camera.password or ""
            if not username and camera.rtsp_url:
                username, password = self._extract_rtsp_credentials(camera.rtsp_url)

            onvif_cam = ONVIFCamera(
                camera.ip,
                80,
                username,
                password,
            )
            device_mgmt = onvif_cam.create_devicemgmt_service()
            device_mgmt.SystemReboot()
            return Response(
                {"message": f"Reboot command sent to {camera.name}"},
                status=status.HTTP_202_ACCEPTED,
            )
        except ONVIFError as e:
            return Response(
                {"error": f"ONVIF reboot failed: {str(e)}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except Exception as e:
            return Response(
                {"error": f"Reboot failed: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


_snapshot_log = logging.getLogger("camera.snapshot")


class CameraRTSPSnapshotView(APIView):
    """Snapshot endpoint for the VehicleAI calibration wizard (and any flow
    that needs the freshest possible camera frame).

    Capture order, fail-fast at each step:
      1. Direct RTSP → ffmpeg single-frame grab, 3-second hard timeout.
      2. Frigate `latest.jpg` → 2-second timeout. (For Ring cameras with no
         rtsp_url this is effectively the only path; go2rtc bridges Ring to
         Frigate.)
      3. Cached thumbnail captured during onboarding.

    Returns 503 only if all three sources fail. Sets `X-Snapshot-Source`
    header so the client can distinguish live RTSP from fallback.

    Logging is verbose by design — every failure logs why, so the wizard's
    "snapshot failed to load" reports can be diagnosed from journalctl.
    """

    permission_classes = []
    authentication_classes = []

    # Shared with camera.tasks.SNAPSHOT_DIR via settings — see CAMERA_THUMBNAILS_DIR
    # in hub_controller/settings/common.py. Single source of truth.
    THUMBNAIL_DIR = getattr(
        __import__("django.conf", fromlist=["settings"]).settings,
        "CAMERA_THUMBNAILS_DIR",
        "/root/jupyter-hub-controller/media/thumbnails",
    )
    RTSP_TIMEOUT_S = 3
    FRIGATE_TIMEOUT_S = 2

    def _capture_rtsp(self, rtsp_url, slug):
        """Grab one JPEG frame from the RTSP URL via ffmpeg. Returns bytes or None."""
        if not rtsp_url:
            _snapshot_log.warning("[%s] no rtsp_url on Camera row", slug)
            return None
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            out_path = f.name
        try:
            cmd = [
                "ffmpeg", "-y",
                "-hide_banner", "-loglevel", "error",
                "-rtsp_transport", "tcp",
                # ffmpeg 5.x renamed -stimeout to -timeout (microseconds)
                "-timeout", str(self.RTSP_TIMEOUT_S * 1_000_000),
                "-i", rtsp_url,
                "-frames:v", "1",
                "-q:v", "2",
                out_path,
            ]
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=self.RTSP_TIMEOUT_S + 1,  # hard wall-clock cap
            )
            if proc.returncode != 0:
                # ffmpeg writes useful info to stderr — log truncated last line only
                err_tail = (proc.stderr or b"").decode("utf-8", errors="replace").strip().splitlines()
                _snapshot_log.warning(
                    "[%s] ffmpeg rc=%d: %s",
                    slug, proc.returncode,
                    err_tail[-1] if err_tail else "no stderr",
                )
                return None
            if not os.path.exists(out_path) or os.path.getsize(out_path) < 100:
                _snapshot_log.warning("[%s] ffmpeg produced empty/missing file", slug)
                return None
            with open(out_path, "rb") as f:
                content = f.read()
            _snapshot_log.info("[%s] rtsp capture OK (%d bytes)", slug, len(content))
            return content
        except subprocess.TimeoutExpired:
            _snapshot_log.warning("[%s] ffmpeg wall-clock timeout (>%ds)", slug, self.RTSP_TIMEOUT_S + 1)
            return None
        except Exception as exc:
            _snapshot_log.exception("[%s] ffmpeg exception: %s", slug, exc)
            return None
        finally:
            try:
                os.unlink(out_path)
            except OSError:
                pass

    def _fetch_frigate_latest(self, slug):
        """Frigate fallback. Returns bytes or None."""
        try:
            url = f"http://127.0.0.1:5000/api/{slug}/latest.jpg"
            with urllib.request.urlopen(url, timeout=self.FRIGATE_TIMEOUT_S) as resp:
                content = resp.read()
                if len(content) > 100:
                    _snapshot_log.info("[%s] frigate fallback OK (%d bytes)", slug, len(content))
                    return content
                _snapshot_log.warning("[%s] frigate returned tiny payload (%d bytes)", slug, len(content))
        except Exception as exc:
            _snapshot_log.warning("[%s] frigate fallback failed: %s", slug, exc)
        return None

    def _read_cached_thumbnail(self, slug):
        path = os.path.join(self.THUMBNAIL_DIR, f"{slug}.jpg")
        if os.path.exists(path):
            try:
                with open(path, "rb") as f:
                    return f.read()
            except OSError as exc:
                _snapshot_log.warning("[%s] cached thumbnail unreadable: %s", slug, exc)
        return None

    def get(self, request, slug):
        try:
            camera = Camera.objects.get(slug_name=slug)
        except Camera.DoesNotExist:
            _snapshot_log.warning("[%s] camera not found", slug)
            return HttpResponse(
                json.dumps({"error": f"Camera '{slug}' not found"}),
                status=status.HTTP_404_NOT_FOUND,
                content_type="application/json",
            )

        # 1. RTSP direct (skip for Ring cams — they have no usable rtsp_url)
        if camera.type == CameraType.RTSP and camera.rtsp_url:
            frame = self._capture_rtsp(camera.rtsp_url, slug)
            if frame:
                return HttpResponse(
                    frame,
                    content_type="image/jpeg",
                    headers={"X-Snapshot-Source": "rtsp-direct", "Cache-Control": "no-store"},
                )

        # 2. Frigate fallback
        frame = self._fetch_frigate_latest(slug)
        if frame:
            # Update cached thumbnail opportunistically
            try:
                os.makedirs(self.THUMBNAIL_DIR, exist_ok=True)
                with open(os.path.join(self.THUMBNAIL_DIR, f"{slug}.jpg"), "wb") as f:
                    f.write(frame)
            except OSError:
                pass
            return HttpResponse(
                frame,
                content_type="image/jpeg",
                headers={"X-Snapshot-Source": "frigate-fallback", "Cache-Control": "no-store"},
            )

        # 3. Cached thumbnail
        cached = self._read_cached_thumbnail(slug)
        if cached:
            return HttpResponse(
                cached,
                content_type="image/jpeg",
                headers={"X-Snapshot-Source": "cached-thumbnail", "Cache-Control": "no-store"},
            )

        _snapshot_log.error("[%s] all three snapshot sources failed", slug)
        return HttpResponse(
            json.dumps({"error": "snapshot unavailable from all sources (rtsp/frigate/cache)"}),
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
            content_type="application/json",
        )


class CameraVehicleCalibrationView(APIView):
    """Per-camera VehicleAI calibration: entry-arrow + park-rectangle.

    GET    /cameras/<slug>/vehicle-calibration  → 200 with calibration, 404 if unset
    POST   /cameras/<slug>/vehicle-calibration  → 200 with saved payload
    DELETE /cameras/<slug>/vehicle-calibration  → 204 (clears, falls back to defaults)

    The calibration is consumed by state_detector.py (number_plate_detection
    container) to commit Approaching→Parked and Departing→Departed transitions.
    Frigate is intentionally bypassed for the entry arrow because Frigate's
    ZoneConfig has no line/tripwire primitive — only the park rectangle could
    be pushed to Frigate as a polygon zone (deferred to Phase 2).
    """

    http_method_names = ["get", "post", "delete"]

    def _get_camera(self, slug):
        try:
            return Camera.objects.get(slug_name=slug)
        except Camera.DoesNotExist:
            return None

    def _ensure_vehicle_ai_enabled(self, camera):
        """Vehicle calibration only saveable if license_vehicle_recognition is
        on AND the camera is in the recognition set — either the legacy
        single-camera ForeignKey OR the new multi-camera M2M (2026-05-03).
        """
        # Legacy single-camera path
        if CameraSetting.objects.filter(
            license_vehicle_recognition=True,
            vehicle_recognition_camera=camera,
        ).exists():
            return True
        # Multi-camera M2M path
        if CameraSetting.objects.filter(
            license_vehicle_recognition=True,
            vehicle_recognition_cameras=camera,
        ).exists():
            return True
        return False

    def get(self, request, slug):
        camera = self._get_camera(slug)
        if camera is None:
            return Response(
                {"error": f"Camera '{slug}' not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        payload = VehicleCalibrationSerializer.from_camera(camera)
        if payload is None:
            return Response(
                {"error": "Calibration not set for this camera."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(payload, status=status.HTTP_200_OK)

    def post(self, request, slug):
        camera = self._get_camera(slug)
        if camera is None:
            return Response(
                {"error": f"Camera '{slug}' not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not self._ensure_vehicle_ai_enabled(camera):
            return Response(
                {
                    "error": (
                        "VehicleAI is not enabled for this camera. Enable "
                        "license_vehicle_recognition with vehicle_recognition_camera "
                        "set to this camera before saving calibration."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = VehicleCalibrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        had_zone_before = camera.vehicle_detection_zone is not None
        VehicleCalibrationSerializer.apply_to_camera(camera, serializer.validated_data)
        # CW#172 — Frigate config flows through camera/templates/frigate_config.yaml
        # via the Celery render task. Re-render only when the detection_zone
        # actually changed (avoids unnecessary Frigate restarts on arrow/park-rect
        # tweaks that don't affect Frigate's view of the world).
        zone_now = camera.vehicle_detection_zone is not None
        zone_changed_value = "detection_zone" in serializer.validated_data
        if zone_changed_value or (had_zone_before != zone_now):
            from camera.tasks import update_frigate_config
            update_frigate_config.delay()
        return Response(
            VehicleCalibrationSerializer.from_camera(camera),
            status=status.HTTP_200_OK,
        )

    def delete(self, request, slug):
        """Clear all vehicle calibration on this camera AND remove it from the
        vehicle-recognition selection. If this was the last camera with vehicle
        AI enabled, also disable license_vehicle_recognition globally.

        Per Temi 2026-05-03: a camera without a detection zone shouldn't run
        vehicle AI; deleting a zone is the explicit signal to also disable
        recognition for that camera. Cascade prevents stranded "enabled but
        unconfigured" state.
        """
        camera = self._get_camera(slug)
        if camera is None:
            return Response(
                {"error": f"Camera '{slug}' not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        had_zone = camera.vehicle_detection_zone is not None
        VehicleCalibrationSerializer.clear_on_camera(camera)

        # Cascade: remove this camera from any CameraSetting's recognition set.
        for setting in CameraSetting.objects.filter(
            license_vehicle_recognition=True,
        ):
            changed = False
            if setting.vehicle_recognition_camera_id == camera.id:
                setting.vehicle_recognition_camera = None
                changed = True
            if setting.vehicle_recognition_cameras.filter(id=camera.id).exists():
                setting.vehicle_recognition_cameras.remove(camera)
                changed = True
            # If no cameras remain in EITHER path, disable the feature globally
            # for this setting — prevents stranded "enabled but no targets" state.
            if changed:
                if (setting.vehicle_recognition_camera is None
                        and not setting.vehicle_recognition_cameras.exists()):
                    setting.license_vehicle_recognition = False
                setting.save()

        if had_zone:
            from camera.tasks import update_frigate_config
            update_frigate_config.delay()
        return Response(status=status.HTTP_204_NO_CONTENT)


class CameraOnvifProbeView(APIView):
    """POST /cameras/<slug>/probe-onvif

    Re-probe ONVIF metadata (manufacturer + model) for an existing camera row.
    Useful when a camera was added without credentials initially, then the user
    saved working creds via the settings UI and the original probe (which fires
    inline during RTSPDiscoverView/Create) didn't run.

    Body (optional):
        {
          "username": "...",  // override stored creds; otherwise uses Camera row's
          "password": "..."
        }

    Response 200:
        {"manufacturer": "Hikvision", "model": "DS-2CD..."}
    Response 404 if camera not found, 400 if not RTSP type, 502 if ONVIF unreachable.
    """

    def post(self, request, slug):
        try:
            camera = Camera.objects.get(slug_name=slug)
        except Camera.DoesNotExist:
            return Response(
                {"error": f"Camera with slug '{slug}' not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        if camera.type != CameraType.RTSP:
            return Response(
                {
                    "error": (
                        f"ONVIF probe only applies to RTSP cameras "
                        f"(this one is {camera.type}). "
                        f"For Ring cameras, manufacturer/model are derived "
                        f"from the Ring API path, not ONVIF."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not camera.ip:
            return Response(
                {"error": "Camera has no IP address recorded — cannot probe"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Allow body to override stored creds (handy if Flutter just collected
        # them and hasn't persisted yet).
        username = request.data.get("username") or camera.username or ""
        password = request.data.get("password") or camera.password or ""

        result = RTSPCamera.objects.get_onvif_device_info(
            camera.ip, username=username, password=password
        )
        if not result:
            return Response(
                {
                    "error": (
                        f"ONVIF probe to {camera.ip} returned no device info. "
                        f"Camera may be unreachable, ONVIF disabled, or creds wrong."
                    )
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )

        camera.onvif_manufacturer = result["manufacturer"]
        camera.onvif_model = result["model"]
        camera.save(update_fields=["onvif_manufacturer", "onvif_model", "updated_at"])

        return Response(
            {
                "slug": slug,
                "manufacturer": result["manufacturer"],
                "model": result["model"],
            },
            status=status.HTTP_200_OK,
        )
