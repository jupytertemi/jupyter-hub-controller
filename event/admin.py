from django.contrib import admin

from event.models import Event


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "event_id",
        "label",
        "sub_label",
        "snapshot_path",
        "video_path",
        "audio_path",
        "camera_name",
        "face_embeddings",
        "confidence_score",
        "loitering",
        "additional_info",
        "parcel_status",
        "is_ignore_suggested_face",
        "is_updated_known_face",
        "parcel_status",
        "vehicle_status",
    )
