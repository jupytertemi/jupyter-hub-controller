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


class EventDetailSerializer(EventSerializer):
    hls_url = serializers.SerializerMethodField()
    local_url = serializers.SerializerMethodField()
    snapshot_url = serializers.SerializerMethodField()

    class Meta(EventSerializer.Meta):
        fields = "__all__"

    def get_snapshot_url(self, obj):
        path = obj.snapshot_path
        if not path:
            return ""
        host = ""
        try:
            host = read_env_file("REMOTE_HOST")
        except Exception:
            return ""
        if not host:
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
            clean = path.replace("/usr/src/app/", "", 1)
            return f"https://{host}/local/vehicle_detection/{clean}"
        return ""

    def get_hls_url(self, obj):
        # Only return HLS URL when no local clip is available — HLS serves HEVC
        # recordings which browsers reject (hev1). Local clips are transcoded to
        # H.264 by clip_transcoder sidecar and are the preferred playback source.
        if obj.video_path:
            return ""
        host = ""
        try:
            host = read_env_file("REMOTE_HOST")
        except Exception as e:
            logging.error(f"read cloudflared fail: {e}")
        if host and host != "":
            host = "https://" + host
            value = f"{host}/frigate/vod/event/{obj.event_id}/index-v1.m3u8"
            if obj.label == "PARCEL":
                value = f"{host}/frigate/vod/event/{obj.parcel_id}/index-v1.m3u8"
            logging.info(f"get_hls_url (fallback): {value}")
            return value
        return ""

    def get_local_url(self, obj):
        logging.info(f"video_path raw: {obj.video_path}")
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

            # ===== DEFAULT =====
            else:
                value = obj.video_path
            logging.info(f"get_local_url: {value}")
            return value
        return ""
