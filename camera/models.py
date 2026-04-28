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
