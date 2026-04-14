from django.core.validators import MinValueValidator
from django.db import models

from alarm.enums import (
    AlarmAudioMode,
    AlarmLedMode,
    AlarmMode,
    AlarmType,
    MicrophoneSensitive,
    OccupancyIllusion,
    PowerEqualizer,
    VolumeEqualizer,
)
from alarm.managers import AlarmDeviceConfigManager, AlarmDeviceManager
from core.models import BaseModel


class AlarmDevice(BaseModel):
    name = models.CharField(max_length=256, default="")
    identity_name = models.CharField(max_length=256, default="", unique=True)
    hass_entry_id = models.CharField(max_length=256, default="")
    type = models.CharField(
        max_length=16,
        choices=AlarmType.choices,
        default=AlarmType.INDOOR,
    )
    version_fw = models.CharField(max_length=64, default="", blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True, help_text="Current IP address of the alarm device")
    mac_address = models.CharField(max_length=17, default="", blank=True, help_text="MAC address in format aa:bb:cc:dd:ee:ff")

    objects = AlarmDeviceManager()


class AlarmDeviceConfig(BaseModel):
    device = models.OneToOneField(
        AlarmDevice,
        on_delete=models.CASCADE,
        related_name="alarm_device",
        null=True,
        blank=True,
    )
    mic_enabled = models.BooleanField(default=False)
    alarm_mode = models.CharField(
        max_length=32,
        choices=AlarmMode.choices,
        default=AlarmMode.OFF,
    )
    volume = models.IntegerField(default=50, validators=[MinValueValidator(0)])

    unusual_sound_enabled = models.BooleanField(default=False)
    voice_ai_enabled = models.BooleanField(default=False)
    smart_announcement_enabled = models.BooleanField(default=False)
    loiter_led = models.CharField(
        max_length=32,
        choices=AlarmLedMode.choices,
        default=AlarmLedMode.RED,
    )
    unusual_sound_led = models.CharField(
        max_length=32,
        choices=AlarmLedMode.choices,
        default=AlarmLedMode.RED,
    )
    parcel_detect_led = models.CharField(
        max_length=32,
        choices=AlarmLedMode.choices,
        default=AlarmLedMode.RED,
    )

    occupancy_illusion = models.CharField(
        max_length=32, choices=OccupancyIllusion.choices, default=OccupancyIllusion.OFF
    )
    audio_mode = models.CharField(
        max_length=16,
        choices=AlarmAudioMode.choices,
        default=AlarmAudioMode.NORMAL,
    )
    volume_equalizer = models.CharField(
        max_length=16,
        choices=VolumeEqualizer.choices,
        default=VolumeEqualizer.BASS,
    )
    power_equalizer = models.CharField(
        max_length=16,
        choices=PowerEqualizer.choices,
        default=PowerEqualizer.STANDBY,
    )
    microphone_sensitive = models.CharField(
        max_length=16,
        choices=MicrophoneSensitive.choices,
        default=MicrophoneSensitive.HIGH,
    )
    objects = AlarmDeviceConfigManager()
