from django.contrib.postgres.fields import ArrayField
from django.db import models

from camera.enums import CameraType, CameraZoneObjectType
from camera.managers import CameraSettingManager, RingCameraManager, RTSPCameraManager
from core.models import BaseModel
from ring.models import RingAccount


class Camera(BaseModel):
    name = models.CharField(max_length=256, null=True)
    username = models.CharField(max_length=256, null=True)
    password = models.CharField(max_length=256, null=True)
    ip = models.CharField(max_length=256, null=True)
    mac_address = models.CharField(max_length=17, null=True, blank=True)
    rtsp_url = models.TextField(null=True)
    sub_rtsp_url = models.TextField(null=True, blank=True)
    ring_account = models.ForeignKey(RingAccount, on_delete=models.SET_NULL, null=True)
    ring_id = models.CharField(max_length=256, null=True)
    ring_device_id = models.CharField(max_length=256, null=True)
    is_audio = models.BooleanField(default=False)
    zone = models.CharField(max_length=256, null=True)

    type = models.CharField(
        max_length=16,
        choices=CameraType.choices,
        default=CameraType.RTSP,
    )
    slug_name = models.CharField(max_length=255, null=False, blank=False, unique=True)
    detect_zone = models.BooleanField(default=False)

    is_enabled = models.BooleanField(default=True)
    consecutive_failures = models.PositiveIntegerField(default=0)
    last_seen_at = models.DateTimeField(blank=True, null=True)
    onvif_manufacturer = models.CharField(max_length=256, null=True, blank=True)
    onvif_model = models.CharField(max_length=256, null=True, blank=True)

    # 2026-05-01 — VehicleAI per-camera calibration (entry arrow + park rectangle).
    # Consumed by state_detector.py to commit Approaching→Parked and Departing→Departed
    # state transitions for known plates. All coordinates are normalized 0-1.
    vehicle_entry_point_x = models.FloatField(null=True, blank=True)
    vehicle_entry_point_y = models.FloatField(null=True, blank=True)
    vehicle_approach_angle_deg = models.FloatField(null=True, blank=True)
    vehicle_park_polygon = models.JSONField(null=True, blank=True)

    # 2026-05-03 — Vehicle detection zone (foundation). 4-point quad, normalized 0-1.
    # Drawn first in the wizard; everything else (arrow + park rectangle) layers on top.
    # AI engine gate: points-in-polygon(bbox_center) before any state logic. Frigate
    # config also publishes this as zones.vehicle_detection_zone for upstream filtering.
    vehicle_detection_zone = models.JSONField(null=True, blank=True)

    # 2026-05-05 — Authoritative stream resolutions probed at onboard time.
    # Captured by reading one snapshot per profile (ONVIF GetSnapshotUri or RTSP
    # first-frame). Drives the Frigate template: detect role pulls the main stream
    # at min(native, 1920) for vehicle-recognition cameras, else falls back to
    # sub stream as before. Never upscale — 480p stays 480p, 720p stays 720p.
    main_stream_width = models.PositiveIntegerField(null=True, blank=True)
    main_stream_height = models.PositiveIntegerField(null=True, blank=True)
    sub_stream_width = models.PositiveIntegerField(null=True, blank=True)
    sub_stream_height = models.PositiveIntegerField(null=True, blank=True)

    # 2026-05-06 — Car-outline calibration step (PR-spec-vehicle-ai-car-outline.md).
    # User drags a 4-corner box matching the parked car's footprint during the
    # wizard; AI engine + verdict UI infer expected plate width in pixels from
    # this + the existing approach-arrow direction. plate_readability_px is the
    # client-computed estimate persisted at save time. plate_ocr_skip is set
    # true when the verdict was yellow ("plate too small but proceed anyway")
    # so the AI engine knows to track bbox without running OCR for this camera.
    # NULL on pre-existing cameras → behave as today (no change).
    vehicle_car_outline = models.JSONField(null=True, blank=True)
    vehicle_plate_readability_px = models.FloatField(null=True, blank=True)
    vehicle_plate_ocr_skip = models.BooleanField(default=False)


class RTSPCamera(Camera):
    objects = RTSPCameraManager()

    class Meta:
        proxy = True

    def save(self, *args, **kwargs):
        self.type = CameraType.RTSP
        return super().save(*args, **kwargs)


class RingCamera(Camera):
    objects = RingCameraManager()

    class Meta:
        proxy = True

    def save(self, *args, **kwargs):
        self.type = CameraType.RING
        return super().save(*args, **kwargs)


class CameraSetting(BaseModel):
    enable_parcel_detect = models.BooleanField(default=False)
    parcel_detect_camera = models.ForeignKey(
        Camera,
        on_delete=models.SET_NULL,
        related_name="parcel_detect_camera_setting",
        null=True,
        blank=True,
    )

    enable_face_recognition = models.BooleanField(default=True)
    loitering_recognition = models.BooleanField(default=False)
    loitering_camera = models.ForeignKey(
        Camera,
        on_delete=models.SET_NULL,
        related_name="loitering_camera_setting",
        null=True,
        blank=True,
    )
    loitering_cameras = models.ManyToManyField(
        Camera,
        related_name="loitering_cameras_setting",
        blank=True,
    )
    license_vehicle_recognition = models.BooleanField(default=True)
    vehicle_recognition_camera = models.ForeignKey(
        Camera,
        on_delete=models.SET_NULL,
        related_name="vehicle_recognition_camera_setting",
        null=True,
        blank=True,
    )
    # 2026-05-03 — Multi-camera support (mirrors loitering_cameras pattern).
    # The legacy ForeignKey above is kept populated to whichever camera was
    # last single-selected, but write-path consumers should prefer the M2M.
    vehicle_recognition_cameras = models.ManyToManyField(
        Camera,
        related_name="vehicle_recognition_cameras_setting",
        blank=True,
    )
    activate_sounds_detection = models.BooleanField(default=False)
    footage_retention_period = models.BooleanField(default=False)
    objects = CameraSettingManager()


class CameraOrganization(BaseModel):
    mac_address_prefix = models.CharField(max_length=256, null=True)
    organization_name = models.CharField(max_length=256, null=True)


class CameraSettingZone(models.Model):
    camera = models.ForeignKey(
        Camera,
        to_field="slug_name",
        db_column="camera_slug",
        related_name="camera_setting_zone",
        on_delete=models.CASCADE,
        null=True,
    )
    zone_name = models.CharField(max_length=256, null=True)
    coordinates = models.JSONField(default=list)
    objects_detect = ArrayField(
        base_field=models.CharField(
            max_length=20,
            choices=CameraZoneObjectType.choices,
        ),
        default=list,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]
