from django.db import models

from core.models import BaseModel
from meross.managers import MerossCloudAccountManager, MerossDeviceManager


class MerossDevice(BaseModel):
    name = models.CharField(max_length=256, default="")
    hass_entry_id = models.CharField(max_length=256, default="")
    objects = MerossDeviceManager()


class RegionType(models.TextChoices):
    AP = "ap"
    EU = "eu"
    US = "us"


class MerossCloudAccount(BaseModel):
    cloud_region = models.CharField(
        max_length=256,
        choices=RegionType.choices,
        default=None,
        null=True,
    )
    hass_entry_id = models.CharField(max_length=256, default="")
    email = models.CharField(max_length=256, default="", unique=True)
    objects = MerossCloudAccountManager()
