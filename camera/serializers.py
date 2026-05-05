import ipaddress
import json
import re

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


def extract_ip_from_rtsp_url(url):
    """Extract a literal IPv4/IPv6 host from an RTSP URL, or None.

    Why: monitor_camera_ips() pings camera.ip every 5 min and disables
    cameras whose ip is NULL. The RTSP serializer historically excluded
    `ip` from input, so freshly-added cameras had ip=NULL → watchdog
    flipped them offline within minutes → Frigate config rendered empty
    → cameras disappeared from streams. Mirror of the same logic in
    migration 0024_backfill_camera_ip_from_rtsp_url so the helper is
    self-contained when migrations run on older Django imports.

    Handles passwords containing '@' (rsplit on last '@' is the userinfo
    separator). Returns None for hostname-based URLs — the watchdog uses
    IP literals for ping/ARP, hostnames don't fit that contract.
    """
    if not url:
        return None
    s = url.split("://", 1)[1] if "://" in url else url
    if "@" in s:
        s = s.rsplit("@", 1)[1]
    host = re.split(r"[:/]", s, 1)[0]
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        return None


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

    def create(self, validated_data):
        ip = extract_ip_from_rtsp_url(validated_data.get("rtsp_url"))
        if ip:
            validated_data["ip"] = ip
        return super().create(validated_data)


class RingCameraSerializer(BaseCameraSerializer):
    class Meta:
        model = RingCamera
        exclude = ("username", "password", "ip", "type")
        extra_kwargs = {
            "id": {"read_only": True},
            "slug_name": {"read_only": True},
            "stream_url": {"read_only": True},
        }

    def create(self, validated_data):
        # Ring cameras don't speak ONVIF (cloud-only via ring_mqtt). Auto-populate
        # the onvif_* fields anyway so the Flutter dashboard's "Manufacturer/Model"
        # rows render something useful instead of null. Model defaults to a generic
        # label; once ring_mqtt's <id>/info MQTT topic is wired into a Celery task,
        # we can swap in the precise Ring product name (Doorbell Pro / Battery
        # Doorbell Plus / Spotlight Cam etc).
        validated_data.setdefault("onvif_manufacturer", "Ring")
        validated_data.setdefault("onvif_model", "Ring Camera")
        return super().create(validated_data)


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
            # 2026-05-03 — Accept either the legacy single-camera FK OR the
            # multi-camera M2M (matches the loitering pattern above). Flutter
            # v162 wizard PATCHes both: per-camera FK during the loop (auth)
            # plus the final M2M set (AI engine consumption layer).
            has_single = attrs.get("vehicle_recognition_camera", None) is not None
            has_multi = bool(attrs.get("vehicle_recognition_cameras", []))
            if not has_single and not has_multi:
                raise ValidationError(
                    "At least one vehicle recognition camera is required "
                    "(vehicle_recognition_camera or vehicle_recognition_cameras)."
                )
        validated_data = super().validate(attrs)
        return validated_data


class VehicleCalibrationSerializer(serializers.Serializer):
    """Per-camera VehicleAI calibration. All five fields are independently
    optional, but they form two semantic groups:

      * detection_zone  — foundation 4-point quad. AI + Frigate gate on this.
      * arrow + park    — entry_point_x/y + approach_angle_deg + park_polygon.
                           Drive the Approaching → Parked → Departing transitions.

    Valid POST shapes (per Flutter v162 wizard, 2026-05-03):

      * zone-only           {"detection_zone": [[x,y]×4]}
      * full-tracking       {"detection_zone":..., "entry_point_x":..., "entry_point_y":...,
                             "approach_angle_deg":..., "park_polygon":[[x,y]×4]}
      * legacy-no-zone      {"entry_point_x":..., "entry_point_y":...,
                             "approach_angle_deg":..., "park_polygon":[[x,y]×4]}

    Constraints:
      * At least one of (detection_zone, full-arrow-and-park-set) must be
        present — empty body is rejected.
      * Arrow + park is all-or-nothing: any one of the four legacy fields
        present requires all four to be present (and the collapsed-geometry
        check still applies).
      * Field present with explicit ``null`` is a "clear this field" instruction
        on PATCH-style writes. Field MISSING from the body leaves the row
        untouched.
    """

    entry_point_x = serializers.FloatField(min_value=0.0, max_value=1.0,
                                           required=False, allow_null=True)
    entry_point_y = serializers.FloatField(min_value=0.0, max_value=1.0,
                                           required=False, allow_null=True)
    approach_angle_deg = serializers.FloatField(min_value=0.0,
                                                required=False, allow_null=True)
    park_polygon = serializers.ListField(
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
    # 2026-05-03 — Foundation zone. 4-point quad, normalized 0-1. When present,
    # AI engine + Frigate gate detection on points-in-polygon before any
    # state-machine logic. The new Flutter wizard always writes this; legacy
    # clients omit it (field is optional).
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

    _LEGACY_FIELDS = (
        "entry_point_x", "entry_point_y", "approach_angle_deg", "park_polygon",
    )

    def validate_approach_angle_deg(self, value):
        if value is not None and value >= 360.0:
            raise ValidationError("approach_angle_deg must be in [0, 360).")
        return value

    def validate_park_polygon(self, value):
        if value is None:
            return value
        # ListField already enforces 4 [x,y] pairs in [0,1].
        # Axis-alignment with corner order TL, TR, BR, BL.
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
        # 2026-05-03 — Two independent groups; legacy is all-or-nothing.
        has_zone = attrs.get("detection_zone") is not None
        legacy_present = [f for f in self._LEGACY_FIELDS if attrs.get(f) is not None]
        has_full_legacy = len(legacy_present) == len(self._LEGACY_FIELDS)
        legacy_partial = 0 < len(legacy_present) < len(self._LEGACY_FIELDS)

        # Partial legacy is invalid whether zone is present or not — broken state.
        if legacy_partial:
            missing = [f for f in self._LEGACY_FIELDS if attrs.get(f) is None]
            raise ValidationError(
                f"Arrow + park calibration is all-or-nothing. Missing: {missing}. "
                "Either provide all four (entry_point_x, entry_point_y, "
                "approach_angle_deg, park_polygon), or omit them all."
            )

        # At least one valid group must be present.
        if not has_zone and not has_full_legacy:
            raise ValidationError(
                "At least one of detection_zone or the full arrow+park set "
                "(entry_point_x, entry_point_y, approach_angle_deg, park_polygon) "
                "must be provided."
            )

        # Collapsed-geometry guard only if we have the full legacy set.
        if has_full_legacy:
            ex = attrs["entry_point_x"]
            ey = attrs["entry_point_y"]
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
        """Field PRESENT → write the value (incl. None=clear).
        Field MISSING from validated_data → leave the column untouched."""
        update_fields = []
        field_map = {
            "entry_point_x": "vehicle_entry_point_x",
            "entry_point_y": "vehicle_entry_point_y",
            "approach_angle_deg": "vehicle_approach_angle_deg",
            "park_polygon": "vehicle_park_polygon",
            "detection_zone": "vehicle_detection_zone",
        }
        for serializer_field, model_field in field_map.items():
            if serializer_field in validated_data:
                setattr(camera, model_field, validated_data[serializer_field])
                update_fields.append(model_field)
        if update_fields:
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

