import logging

from rest_framework import serializers

from event.models import Event
from utils.update_env import read_env_file


class EventSerializer(serializers.ModelSerializer):
    class Meta:
        model = Event
        fields = "__all__"
        extra_kwargs = {
            "id": {"read_only": True},
        }


class EventVerdictSerializer(serializers.Serializer):
    """PATCH /api/events/{event_id}/verdict (Helios Tier 1 §3.1).

    Caller-supplied identity (`verdict_by_name`) per the project's no-Django-
    auth convention — Helios knows its logged-in user client-side and we
    record whatever string they pass. To CLEAR an existing verdict, PATCH
    with verdict=null; the view nulls all four columns including timestamp
    and actor name. Note has a 240-char ceiling per spec.
    """
    verdict = serializers.ChoiceField(
        choices=["resolved", "watch", "false_alarm"],
        required=False, allow_null=True,
    )
    note = serializers.CharField(
        required=False, allow_null=True, allow_blank=True, max_length=240,
    )
    by_name = serializers.CharField(
        required=False, allow_null=True, allow_blank=True, max_length=120,
    )

    def validate(self, attrs):
        # Reject empty body — must include at least verdict (even verdict=null
        # to clear). Forces the caller to be explicit about intent.
        if "verdict" not in attrs:
            raise serializers.ValidationError(
                "Body must include 'verdict' (use null to clear)."
            )
        return attrs


class EventDetailSerializer(EventSerializer):
    hls_url = serializers.SerializerMethodField()
    local_url = serializers.SerializerMethodField()
    snapshot_url = serializers.SerializerMethodField()

    class Meta(EventSerializer.Meta):
        fields = "__all__"

    def get_snapshot_url(self, obj):
        path = obj.snapshot_path
        host = ""
        try:
            host = read_env_file("REMOTE_HOST")
        except Exception:
            return ""
        if not host:
            return ""
        if not path:
            return ""
        if path.startswith("http://frigate:5000/"):
            return path.replace(
                "http://frigate:5000/",
                f"https://{host}/frigate/",
                1
            )
        if path.startswith("/media/frigate/"):
            return path.replace(
                "/media/frigate/",
                f"https://{host}/local/",
                1
            )
        if path.startswith("debug/") or path.startswith("/usr/src/app/debug/"):
            return f"https://{host}/frigate/api/events/{obj.event_id}/snapshot.jpg"
        return ""

    def get_hls_url(self, obj):
        host = ""
        try:
            host = read_env_file("REMOTE_HOST")
        except Exception as e:
            logging.error(f"read cloudflared fail: {e}")
        if host and host != "":
            host = "https://" + host
            value = f"{host}/frigate/vod/event/{obj.event_id}/index-v1.m3u8"
            return value
        return ""

    def get_local_url(self, obj):
        logging.info(f"video_path raw: {obj.video_path}")
        if not obj.video_path:
            return ""
        host = ""
        prefix = "/media/frigate/"
        try:
            host = read_env_file("REMOTE_HOST")
        except Exception as e:
            logging.error(f"read cloudflared fail: {e}")
        logging.info(f"cloudflared host: {host}")
        if host and host != "":
            if prefix in obj.video_path:
                host = "https://" + host
                replace_prefix = f"{host}/local/"
                value = obj.video_path.replace(prefix, replace_prefix)
            # ===== CASE 2: frigate internal API =====
            elif obj.video_path.startswith("http://frigate:5000/api/"):
                value = obj.video_path.replace(
                    "http://frigate:5000/api/",
                    f"https://{host}/frigate/api/",
                    1
                )

            # ===== CASE 3: vehicle AI debug paths =====
            elif obj.video_path.startswith("debug/") or obj.video_path.startswith("/usr/src/app/debug/"):
                clean = obj.video_path.replace("/usr/src/app/", "", 1)
                value = f"https://{host}/local/vehicle_detection/{clean}"

            # ===== DEFAULT =====
            else:
                value = obj.video_path
            logging.info(f"get_local_url: {value}")
            return value
        return ""
