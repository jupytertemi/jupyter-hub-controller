import uuid

from django.db import models

from core.models import BaseModel
from facial.managers import FacialManager


class Facial(BaseModel):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    name = models.CharField(max_length=256, null=True)
    video_url = models.TextField(null=True)
    is_ignore = models.BooleanField(default=False)
    processing = models.CharField(max_length=256, default="In processing")
    avatar = models.TextField("avatar", blank=True, null=True)
    objects = FacialManager()

    def __str__(self):
        return f"{self.id}-{self.name}"
