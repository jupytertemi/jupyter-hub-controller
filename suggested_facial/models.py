from django.db import models

from core.models import BaseModel
from facial.models import Facial
from suggested_facial.managers import SuggestedFacialManager


class SuggestedFacial(BaseModel):
    person = models.ForeignKey(
        Facial, on_delete=models.CASCADE, related_name="suggested_face", unique=True
    )
    suggested_name = models.CharField(max_length=256, null=True, blank=True)
    face_embeddings = models.TextField(null=True)
    confidence = models.FloatField(default=0.0)
    face_embeddings_2 = models.TextField(null=True)
    confidence_2 = models.FloatField(default=0.0)
    face_embeddings_3 = models.TextField(null=True)
    confidence_3 = models.FloatField(default=0.0)
    total_times = models.IntegerField(default=0)
    distinct_days = models.IntegerField(default=0)
    title = models.CharField(max_length=256, default="")
    is_ignore = models.BooleanField(default=False)
    is_almost = models.BooleanField(default=False)
    objects = SuggestedFacialManager()

    def __str__(self):
        return f"{self.person}-{self.suggested_name}"
