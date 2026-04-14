from django.db import models

from core.models import BaseModel
from ring.managers import RingAccountManager


class RingAccount(BaseModel):
    username = models.CharField(max_length=256, unique=True)
    token = models.TextField()
    is_valid = models.BooleanField(default=True)

    objects = RingAccountManager()
