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
