import json

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from camera.enums import CameraType
from camera.models import (
    Camera,
    CameraSetting,
    CameraSettingZone,
    RingCamera,
    RTSPCamera,
)
from utils.exceptions import CustomException


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
        return f"http://{settings.REMOTE_HOST}/webrtc/{obj.slug_name}/whep"


class RTSPCameraSerializer(BaseCameraSerializer):
    class Meta:
        model = RTSPCamera
        exclude = ("ring_account", "ring_id", "type", "username", "password", "ip")
        extra_kwargs = {
            "id": {"read_only": True},
            "slug_name": {"read_only": True},
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
    class Meta:
        model = Camera
        fields = "__all__"
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
            if (
                not attrs.get("loitering_camera", None)
                or attrs.get("loitering_camera", None) is None
            ):
                raise ValidationError("This 'loitering_recognition' field is required.")

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
