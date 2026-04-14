from django.db import models

from core.models import BaseModel
from external_device.enum import ExternalDeviceStatus, ExternalType


class ExternalDevice(BaseModel):
    mac_address = models.CharField(max_length=255, db_index=True)
    name = models.CharField(max_length=255)
    type = models.CharField(
        max_length=32,
        choices=ExternalType.choices,
        default=ExternalType.S1,
    )
    status = models.CharField(
        max_length=16,
        choices=ExternalDeviceStatus.choices,
        default=ExternalDeviceStatus.PENDING,
    )
    socket_response = models.JSONField(default=dict, blank=True)
