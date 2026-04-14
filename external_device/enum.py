from django.db import models
from django.utils.translation import gettext_lazy as _


class ExternalType(models.TextChoices):
    S1 = "S1", _("S1")
    K11 = "K11", _("K11")


class ExternalDeviceStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    SUCCESS = "success", _("Success")
    FAILED = "failed", _("Failed")
