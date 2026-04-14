from django.db import models
from pgvector.django import VectorField

from core.models import BaseModel
from facial.models import Facial


class FaceTraining(BaseModel):
    person = models.ForeignKey(
        Facial,
        on_delete=models.CASCADE,
        related_name="trainings",
    )
    is_ignore = models.BooleanField(default=False)
    person_name = models.CharField(max_length=256, null=True)
    embedding = VectorField(dimensions=512, null=True, blank=True)
    quality_score = models.FloatField(default=0.0, null=True, blank=True)
    augmentation_type = models.CharField(max_length=64, null=True, blank=True)

    def __str__(self):
        return f"Training-{self.id} for {self.person.name}"
