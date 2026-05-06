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

    # 2026-05-06 — Helios Tier 1 §3.1: forensic verdicts.
    # Owner-operators mark events as resolved/watch/false_alarm with an
    # optional note. Helios renders the verdict chip on the events list
    # using these fields directly (so no per-row lookup). PATCH semantics:
    # passing verdict=null clears all four fields. verdict_by_name is a
    # caller-asserted display name (Helios knows the logged-in user
    # client-side); we don't hard-link to Django's User model so the field
    # works without a project-wide auth deployment.
    VERDICT_CHOICES = [
        ("resolved", "Resolved"),
        ("watch", "Watch"),
        ("false_alarm", "False alarm"),
    ]
    verdict = models.CharField(
        max_length=16, choices=VERDICT_CHOICES, null=True, blank=True,
    )
    verdict_note = models.TextField(null=True, blank=True)
    verdict_by_name = models.CharField(max_length=120, null=True, blank=True)
    verdict_at = models.DateTimeField(null=True, blank=True)

    objects = EventManager()

    class Meta:
        indexes = [
            models.Index(fields=["label"]),
            models.Index(fields=["start_time"]),
            models.Index(fields=["end_time"]),
            models.Index(fields=["created_at"]),
            # 2026-05-06 — Helios verdict-list filters
            models.Index(fields=["verdict"]),
            models.Index(fields=["verdict_at"]),
        ]
