from django_filters import rest_framework as filters

from event.enums import LabelType
from event.models import Event


class EventFilter(filters.FilterSet):
    label = filters.ChoiceFilter(choices=LabelType.choices)
    created_at = filters.DateFilter(field_name="created_at", lookup_expr="date")
    start_time = filters.DateTimeFilter(field_name="start_time", lookup_expr="gte")
    end_time = filters.DateTimeFilter(field_name="end_time", lookup_expr="gte")

    class Meta:
        model = Event
        fields = [
            "label",
            "created_at",
            "event_id",
            "additional_info",
            "video_path",
            "snapshot_path",
            "audio_path",
            "camera_name",
            "confidence_score",
            "sub_label",
            "title",
            "loitering",
            "is_ignore_suggested_face",
            "is_updated_known_face",
            "parcel_status",
            "vehicle_status",
            "parcel_id",
            "start_time",
            "end_time",
        ]
