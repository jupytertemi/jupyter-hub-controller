from django.contrib.postgres.fields import ArrayField
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver

from alarm.models import AlarmDevice, AlarmDeviceConfig
from automation.enums import (
    AlarmScheduleRepeatType,
    AlarmSettingsMode,
    AlarmSound,
    Weekdays,
)
from automation.managers import AlarmSettingsManager
from core.models import BaseModel


class AlarmSettings(BaseModel):
    device = models.OneToOneField(
        AlarmDevice,
        on_delete=models.CASCADE,
        related_name="alarm_settings_device",
        null=True,
        blank=True,
    )
    mode = models.CharField(
        max_length=32,
        choices=AlarmSettingsMode.choices,
        default=AlarmSettingsMode.TRAVEL,
    )
    schedule = models.BooleanField(default=False)
    schedule_start = models.BigIntegerField(default=0)
    schedule_end = models.BigIntegerField(default=0)
    schedule_repeat = ArrayField(
        default=list,
        null=True,
        blank=True,
        base_field=models.CharField(
            max_length=32, choices=Weekdays.choices, default=None, null=True, blank=True
        ),
    )
    repeat_type = models.CharField(
        max_length=32,
        choices=AlarmScheduleRepeatType.choices,
        default=AlarmScheduleRepeatType.NEVER,
    )
    volume = models.IntegerField(default=50, validators=[MinValueValidator(0)])
    sound = models.CharField(
        max_length=32, choices=AlarmSound.choices, default=AlarmSound.ALARM
    )
    sound_duration = models.IntegerField(default=1, validators=[MinValueValidator(0)])
    delay = models.IntegerField(default=0, validators=[MinValueValidator(0)])

    unusual_sound_activate = models.BooleanField(default=True)
    loitering_activate = models.BooleanField(default=False)
    known_face_disarm = models.BooleanField(default=False)
    live_activity_prompt = models.BooleanField(default=False)
    parcel_theft_activate = models.BooleanField(default=False)
    entry_door_activate = models.BooleanField(default=False)
    entry_door_all_sensors = models.BooleanField(default=True)
    entry_door_exit_delay_seconds = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(180)],
    )
    entry_sensors = models.ManyToManyField(
        "external_device.ExternalDevice",
        blank=True,
        related_name="alarm_settings_entry_sensors",
    )

    objects = AlarmSettingsManager()


@receiver(post_save, sender=AlarmDevice)
def create_default_alarm_device_config(sender, instance, created, **kwargs):
    if created:
        AlarmDeviceConfig.objects.create(device=instance)
        AlarmSettings.objects.create(device=instance, mode=AlarmSettingsMode.NONE)
