import json

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from camera.enums import CameraType
from camera.models import (
    Camera,
    CameraOrganization,
    CameraSetting,
    CameraSettingZone,
    RingCamera,
    RTSPCamera,
)
from utils.exceptions import CustomException
from utils.update_env import read_env_file
import logging


class RTSPDiscoveringSerializer(serializers.Serializer):
    ip = serializers.CharField()
    name = serializers.CharField()


class RTSPCameraUrlSerializer(serializers.Serializer):
    username = serializers.CharField(required=False)
    password = serializers.CharField(required=False)
    ip = serializers.CharField()


class BaseCameraSerializer(serializers.ModelSerializer):
    stream_url = serializers.SerializerMethodField()
    ring_refresh_token = serializers.SerializerMethodField()

    def get_ring_refresh_token(self, obj):
        if obj.type == CameraType.RING.value:
            token = json.loads(obj.ring_account.token)
            return token.get("refresh_token")
        else:
            return None

    def get_stream_url(self, obj):
        host = ""
        try:
            host = read_env_file("REMOTE_HOST")
        except Exception as e:
            logging.error(f"read REMOTE_HOST fail: {e}")
        if not host:
            host = settings.REMOTE_HOST
        return f"https://{host}/webrtc/{obj.slug_name}/whep"


class RTSPCameraSerializer(BaseCameraSerializer):
    class Meta:
        model = RTSPCamera
        exclude = ("ring_account", "ring_id", "type", "username", "password", "ip")
        extra_kwargs = {
            "id": {"read_only": True},
            "slug_name": {"read_only": True},
            "sub_rtsp_url": {"read_only": True},
            "skip_validation": {"write_only": True},
            "rtsp_url": {"required": True},
            "stream_url": {"read_only": True},
        }


class RingCameraSerializer(BaseCameraSerializer):
    class Meta:
        model = RingCamera
        exclude = ("username", "password", "ip", "type")
        extra_kwargs = {
            "id": {"read_only": True},
            "slug_name": {"read_only": True},
            "stream_url": {"read_only": True},
        }


class CameraSerializer(BaseCameraSerializer):
    vendor = serializers.SerializerMethodField()

    CHIPSET_VENDORS = {
        "espressif", "realtek", "hisilicon", "ingenic", "mediatek",
        "qualcomm", "broadcom", "marvell", "ralink", "silicon laboratories",
        "texas instruments", "microchip technology", "nordic semiconductor",
    }

    def get_vendor(self, obj):
        onvif_mfr = getattr(obj, "onvif_manufacturer", None)
        if onvif_mfr and onvif_mfr.strip():
            return onvif_mfr.strip()
        mac = getattr(obj, "mac_address", None)
        if not mac or len(mac) < 8:
            return None
        prefix = mac[:8].upper()
        try:
            org = CameraOrganization.objects.filter(mac_address_prefix=prefix).first()
            if not org:
                return None
            name = org.organization_name
            if any(c in name.lower() for c in self.CHIPSET_VENDORS):
                return f"{name} (OEM)"
            return name
        except Exception:
            return None

    class Meta:
        model = Camera
        fields = [
            "id", "name", "username", "password", "ip", "mac_address",
            "rtsp_url", "sub_rtsp_url", "ring_account", "ring_id",
            "ring_device_id", "is_audio", "zone", "type", "slug_name",
            "detect_zone", "is_enabled", "consecutive_failures",
            "last_seen_at", "created_at", "updated_at",
            "stream_url", "ring_refresh_token", "vendor",
            "onvif_manufacturer", "onvif_model",
        ]
        extra_kwargs = {
            "id": {"read_only": True},
            "slug_name": {"read_only": True},
            "stream_url": {"read_only": True},
        }


class UpdateCameraSerializer(serializers.ModelSerializer):
    class Meta:
        model = Camera
        fields = ["name"]


class CameraSettingZoneSerializer(serializers.ModelSerializer):
    class Meta:
        model = CameraSettingZone
        fields = ["zone_name", "coordinates", "objects_detect"]
        extra_kwargs = {
            "id": {"read_only": True},
        }


class AddCameraSettingZoneSerializer(serializers.Serializer):
    zones = serializers.ListSerializer(child=CameraSettingZoneSerializer())
    camera = serializers.CharField()

    def validate(self, attrs):
        zones = attrs["zones"]

        new_count = len(zones)
        try:
            Camera.objects.get(slug_name=attrs["camera"])
        except ObjectDoesNotExist:
            raise ValidationError("This camera doesn't exist.")
        if new_count > 3:
            raise CustomException("Each camera can only have up to 3 zones.")

        return attrs

    def create(self, validated_data):
        zones_data = validated_data["zones"]
        camera = validated_data["camera"]
        camera_obj = Camera.objects.get(slug_name=camera)
        objs = [CameraSettingZone(**zone, camera=camera_obj) for zone in zones_data]
        with transaction.atomic():
            CameraSettingZone.objects.filter(camera__slug_name=camera).delete()
            if len(objs) > 0:
                camera_obj.detect_zone = True
                camera_obj.save()
                CameraSettingZone.objects.bulk_create(objs)
            else:
                camera_obj.detect_zone = False
                camera_obj.save()

        return objs


class CameraSettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = CameraSetting
        fields = "__all__"

    def validate(self, attrs):
        if attrs.get("enable_parcel_detect", None):
            if (
                not attrs.get("parcel_detect_camera", None)
                or attrs.get("parcel_detect_camera", None) is None
            ):
                raise ValidationError("This 'parcel_detect_camera' field is required.")
        if attrs.get("loitering_recognition", None):
            has_single = attrs.get("loitering_camera", None) is not None
            has_multi = bool(attrs.get("loitering_cameras", []))
            if not has_single and not has_multi:
                raise ValidationError("At least one loitering camera is required.")

        if attrs.get("license_vehicle_recognition", None):
            if (
                not attrs.get("vehicle_recognition_camera", None)
                or attrs.get("vehicle_recognition_camera", None) is None
            ):
                raise ValidationError(
                    "This 'vehicle_recognition_camera' field is required."
                )
        validated_data = super().validate(attrs)
        return validated_data


class VehicleCalibrationSerializer(serializers.Serializer):
    """Serializer for per-camera VehicleAI calibration (entry arrow + park rectangle).

    Saves four pieces of geometry per camera that let state_detector.py commit
    Approaching→Parked and Departing→Departed transitions reliably.
    """

    entry_point_x = serializers.FloatField(min_value=0.0, max_value=1.0)
    entry_point_y = serializers.FloatField(min_value=0.0, max_value=1.0)
    approach_angle_deg = serializers.FloatField(min_value=0.0)
    park_polygon = serializers.ListField(
        child=serializers.ListField(
            child=serializers.FloatField(min_value=0.0, max_value=1.0),
            min_length=2,
            max_length=2,
        ),
        min_length=4,
        max_length=4,
    )
    # 2026-05-03 — Optional foundation zone. 4-point quad, normalized 0-1.
    # When present, AI engine and Frigate gate detection on points-in-polygon
    # before any state-machine logic. New wizards write this; legacy clients
    # may omit it (field is optional).
    detection_zone = serializers.ListField(
        child=serializers.ListField(
            child=serializers.FloatField(min_value=0.0, max_value=1.0),
            min_length=2,
            max_length=2,
        ),
        min_length=4,
        max_length=4,
        required=False,
        allow_null=True,
    )

    def validate_approach_angle_deg(self, value):
        if value >= 360.0:
            raise ValidationError("approach_angle_deg must be in [0, 360).")
        return value

    def validate_park_polygon(self, value):
        # Already shape-validated (4 [x,y] pairs in [0,1]) by ListField config.
        # Enforce axis-alignment with corner order TL, TR, BR, BL.
        tl, tr, br, bl = value
        if tl[0] != bl[0]:
            raise ValidationError("park_polygon not axis-aligned: TL.x != BL.x")
        if tr[0] != br[0]:
            raise ValidationError("park_polygon not axis-aligned: TR.x != BR.x")
        if tl[1] != tr[1]:
            raise ValidationError("park_polygon not axis-aligned: TL.y != TR.y")
        if bl[1] != br[1]:
            raise ValidationError("park_polygon not axis-aligned: BL.y != BR.y")
        if tl[0] >= tr[0]:
            raise ValidationError("park_polygon zero or negative width.")
        if tl[1] >= bl[1]:
            raise ValidationError("park_polygon zero or negative height.")
        return value

    def validate(self, attrs):
        # entry point must NOT lie inside the park rectangle (collapsed geometry).
        ex, ey = attrs["entry_point_x"], attrs["entry_point_y"]
        poly = attrs["park_polygon"]
        min_x = min(p[0] for p in poly)
        max_x = max(p[0] for p in poly)
        min_y = min(p[1] for p in poly)
        max_y = max(p[1] for p in poly)
        if min_x <= ex <= max_x and min_y <= ey <= max_y:
            raise ValidationError(
                "entry_point cannot lie inside park_polygon (collapsed geometry)."
            )
        return attrs

    @staticmethod
    def from_camera(camera):
        if camera.vehicle_entry_point_x is None and camera.vehicle_detection_zone is None:
            return None
        return {
            "entry_point_x": camera.vehicle_entry_point_x,
            "entry_point_y": camera.vehicle_entry_point_y,
            "approach_angle_deg": camera.vehicle_approach_angle_deg,
            "park_polygon": camera.vehicle_park_polygon,
            "detection_zone": camera.vehicle_detection_zone,
        }

    @staticmethod
    def apply_to_camera(camera, validated_data):
        camera.vehicle_entry_point_x = validated_data["entry_point_x"]
        camera.vehicle_entry_point_y = validated_data["entry_point_y"]
        camera.vehicle_approach_angle_deg = validated_data["approach_angle_deg"]
        camera.vehicle_park_polygon = validated_data["park_polygon"]
        update_fields = [
            "vehicle_entry_point_x",
            "vehicle_entry_point_y",
            "vehicle_approach_angle_deg",
            "vehicle_park_polygon",
        ]
        # detection_zone is optional — only update if explicitly present (allows
        # legacy clients to keep working without zeroing the new field).
        if "detection_zone" in validated_data:
            camera.vehicle_detection_zone = validated_data["detection_zone"]
            update_fields.append("vehicle_detection_zone")
        camera.save(update_fields=update_fields)

    @staticmethod
    def clear_on_camera(camera):
        camera.vehicle_entry_point_x = None
        camera.vehicle_entry_point_y = None
        camera.vehicle_approach_angle_deg = None
        camera.vehicle_park_polygon = None
        camera.vehicle_detection_zone = None
        camera.save(update_fields=[
            "vehicle_entry_point_x",
            "vehicle_entry_point_y",
            "vehicle_approach_angle_deg",
            "vehicle_park_polygon",
            "vehicle_detection_zone",
        ])

