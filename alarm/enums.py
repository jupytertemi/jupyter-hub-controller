from django.db import models
from django.utils.translation import gettext_lazy as _


class AlarmType(models.TextChoices):
    OUTDOOR = "OUTDOOR", _("OUTDOOR Alarm")
    INDOOR = "INDOOR", _("INDOOR Alarm")


class AlarmMode(models.TextChoices):
    OFF = "off", _("Off")
    NIGHT = "night", _("Night")
    AWAY = "away", _("Away")


class AlarmLedMode(models.TextChoices):
    PURPLE = "purple", _("Purple")
    RED = "red", _("Red")
    AMBER = "amber", _("Amber")


class AlarmAudioMode(models.TextChoices):
    NORMAL = "normal", _("Normal")
    IMMERSIVE = "immersive", _("Immersive")


class VolumeEqualizer(models.TextChoices):
    FLAT = "flat", _("Flat")
    VOICE = "voice", _("Voice")
    BASS = "bass", _("Bass")


class PowerEqualizer(models.TextChoices):
    NORMAL = "normal", _("Normal")
    LOW = "low", _("Low")
    STANDBY = "standby", _("Standby")


class MicrophoneSensitive(models.TextChoices):
    MEDIUM = "medium", _("Medium")
    LOW = "low", _("Low")
    HIGH = "High", _("High")


class OccupancyIllusion(models.TextChoices):
    OFF = "off", _("")
    PEOPLE = "people", _("People")
    RUNNING_APPLIANCES = "appliances", _("Appliances")
    DOGS = "barking_dogs", _("Dogs")
