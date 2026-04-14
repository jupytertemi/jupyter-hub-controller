from django.db import models
from django.utils.translation import gettext_lazy as _


class CameraType(models.TextChoices):
    RTSP = "RTSP", _("RTSP Camera")
    RING = "RING", _("Ring Camera")


class CameraZoneObjectType(models.TextChoices):
    PERSON = "person", _("Person")
    CAR = "car", _("Car")
