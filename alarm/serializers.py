from rest_framework import serializers

from alarm.enums import AlarmMode, OccupancyIllusion
from alarm.models import AlarmDevice, AlarmDeviceConfig
from automation.enums import AlarmSettingsMode, AlarmSound
from automation.models import AlarmSettings


class AlarmDeviceConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = AlarmDeviceConfig
        exclude = ("created_at", "updated_at", "id")


class AlarmDeviceSerializer(serializers.ModelSerializer):
    config = AlarmDeviceConfigSerializer(source="alarm_device", required=False)

    class Meta:
        model = AlarmDevice
        # v1.6: explicit exclude of `device_secret` so it never leaks via
        # the default GET endpoints. Use HaloRecoverySecretView for explicit
        # secret retrieval (auth-gated).
        exclude = ("device_secret",)
        extra_kwargs = {
            "id": {"read_only": True},
            "hass_entry_id": {"read_only": True},
            "created_at": {"read_only": True},
            "updated_at": {"read_only": True},
        }

    def update(self, instance, validated_data):
        config_data = validated_data.pop("alarm_device", None) or {}
        # Build 199 — accept top-level volume from the Flutter slider's
        # flat PUT body ({"volume": N}) and fold it into config_data so
        # the existing settings_payload + AlarmSettings.update_instance
        # path runs. Without this the field was silently dropped because
        # AlarmDevice has no `volume` attr.
        raw_volume = self.initial_data.get("volume") if hasattr(self, "initial_data") else None
        if raw_volume is not None and "volume" not in config_data:
            try:
                config_data["volume"] = int(raw_volume)
            except (TypeError, ValueError):
                pass
        OCCUPANCY_TO_SOUND_MAP = {
            OccupancyIllusion.PEOPLE.value: AlarmSound.PEOPLE_HOME.value,
            OccupancyIllusion.RUNNING_APPLIANCES.value: AlarmSound.RUNNING_APPLIANCES.value,
            OccupancyIllusion.DOGS.value: AlarmSound.BARKING_DOGS.value,
        }
        # update AlarmDevice
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if config_data:
            config, _ = AlarmDeviceConfig.objects.get_or_create(device=instance)
            for attr, value in config_data.items():
                setattr(config, attr, value)

            # Audio settings (volume_equalizer / audio_mode / power_equalizer)
            # push direct to firmware via /{slug}/audio_settings — bypasses
            # HA media_player.volume_set path because those are EQ presets,
            # not media volume. Indoor halos only; outdoor has no DSP.
            audio_payload = {}
            if "volume_equalizer" in config_data:
                audio_payload["volume_equalizer"] = config_data["volume_equalizer"]
            if "audio_mode" in config_data:
                audio_payload["audio_mode"] = config_data["audio_mode"]
            if "power_equalizer" in config_data:
                audio_payload["power_equalizer"] = config_data["power_equalizer"]
            if audio_payload:
                AlarmDeviceConfig.objects.publish_audio_settings(
                    instance.identity_name, audio_payload
                )

            alarm_settings = AlarmSettings.objects.get(device=config.device)
            settings_payload = {}
            if "volume" in config_data:
                settings_payload["volume"] = config_data["volume"]

            if config.alarm_mode != AlarmMode.OFF.value:
                settings_payload.update(
                    {"mode": config.alarm_mode, "sound": AlarmSound.ALARM.value}
                )
            if config.occupancy_illusion != OccupancyIllusion.OFF.value:
                sound = OCCUPANCY_TO_SOUND_MAP.get(config.occupancy_illusion)

                if sound:
                    settings_payload.update(
                        {"mode": AlarmSettingsMode.TRAVEL.value, "sound": sound}
                    )

            if (
                config.occupancy_illusion == AlarmMode.OFF.value
                and config.alarm_mode == AlarmMode.OFF.value
            ):
                settings_payload["mode"] = AlarmSettingsMode.NONE.value

            if settings_payload:
                AlarmSettings.objects.update_instance(
                    alarm_settings,
                    **settings_payload,
                )
            config.save()
            AlarmDeviceConfig.objects.update_config(config)

        return instance


class UpdateAlarmDeviceSerializer(AlarmDeviceSerializer):
    class Meta(AlarmDeviceSerializer.Meta):
        extra_kwargs = {
            **AlarmDeviceSerializer.Meta.extra_kwargs,
            "identity_name": {
                "read_only": True,
            },
            # `type` is now writable on PATCH (was read_only). Build 156 item 7.
            # Allows the Flutter app's saveMetadata flow to PATCH the user's
            # QR-derived INDOOR/OUTDOOR choice onto the auto-created row,
            # which webhook initially defaults to INDOOR. AlarmType TextChoices
            # provides the value-validation gate (only INDOOR/OUTDOOR accepted).
        }


class TurnOnOffAlarmSerializer(serializers.Serializer):
    sound = serializers.ChoiceField(
        choices=AlarmSound.choices, default=AlarmSound.ALARM
    )
    state = serializers.ChoiceField(choices=["on", "off"], default="on")


class AlarmModeSerializer(serializers.Serializer):
    mode = serializers.CharField(required=True)
    device = serializers.CharField(required=True)
    key = serializers.CharField(required=True)
