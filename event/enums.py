from django.db import models
from django.utils.translation import gettext_lazy as _


class LabelType(models.TextChoices):
    PERSON = "PERSON", _("PERSON Event")
    CAR = "CAR", _("CAR Event")
    ANIMAL = "ANIMAL", _("ANIMAL Event")
    AUDIO = "AUDIO", _("AUDIO Event")
    PARCEL = "PARCEL", _("PARCEL Event")
