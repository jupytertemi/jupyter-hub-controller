from django.db import models

from core.models import BaseModel


class Turn(BaseModel):
    id = models.AutoField(primary_key=True)
    uid = models.CharField(max_length=64, db_index=True)
    name = models.CharField(max_length=255)

    credential = models.JSONField()

    previous_turn = models.JSONField(null=True)

    def __str__(self):
        return f"{self.id} - {self.name}"
