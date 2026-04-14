from django.db import models

from automation_garage.managers import GarageDoorSettingsManager
from camera.models import Camera
from core.models import BaseModel
from meross.models import MerossDevice


class GarageDoorSettings(BaseModel):
    garage = models.OneToOneField(
        MerossDevice,
        on_delete=models.CASCADE,
        related_name="auto_garage_setting",
    )
    camera = models.OneToOneField(
        Camera,
        on_delete=models.CASCADE,
        related_name="camera_auto_garage_setting",
        null=True,
    )
    active_open = models.BooleanField(default=False)
    auto_close = models.BooleanField(default=False)
    auto_close_delay = models.IntegerField(default=1)
    auto_open_on_owner = models.BooleanField(default=False)
    card_on_owner = models.BooleanField(default=False)
    card_on_unknown = models.BooleanField(default=False)

    objects = GarageDoorSettingsManager()
