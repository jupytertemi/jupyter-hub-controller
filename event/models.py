from django.db import models
from pgvector.django import VectorField

from core.models import BaseModel
from event.enums import LabelType
from event.managers import EventManager
from facial.models import Facial


class Event(BaseModel):
    event_id = models.CharField(max_length=256, default="", unique=True)
    additional_info = models.TextField(default="")
    label = models.CharField(
        max_length=16,
        choices=LabelType.choices,
        default=LabelType.PERSON,
    )
    video_path = models.CharField(max_length=256, default="")
    snapshot_path = models.CharField(max_length=256, default="")
    audio_path = models.CharField(max_length=256, default="")
    camera_name = models.CharField(max_length=256, default="")
    face_embeddings = models.TextField(null=True)
    embedding = VectorField(dimensions=512, null=True, blank=True)
    confidence_score = models.FloatField(default=0.0)
    sub_label = models.CharField(max_length=256, default="")
    title = models.CharField(max_length=256, default="")
    loitering = models.CharField(max_length=256, default="")
    is_ignore_suggested_face = models.BooleanField(default=False)
    is_updated_known_face = models.BooleanField(default=False)
    parcel_status = models.CharField(max_length=256, default="")
    vehicle_status = models.CharField(max_length=256, default="")
    parcel_id = models.CharField(max_length=256, default="")
    start_time = models.DateTimeField(null=True, blank=True)
    end_time = models.DateTimeField(null=True, blank=True)
    person = models.ForeignKey(
        Facial,
        on_delete=models.SET_NULL,
        related_name="event_face",
        null=True,
        blank=True,
    )
    vehicle_plate = models.CharField(max_length=256, default="")
    objects = EventManager()

    class Meta:
        indexes = [
            models.Index(fields=["label"]),
            models.Index(fields=["start_time"]),
            models.Index(fields=["end_time"]),
            models.Index(fields=["created_at"]),
        ]
