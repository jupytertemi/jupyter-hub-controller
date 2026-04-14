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
        fields = "__all__"
        extra_kwargs = {
            "id": {"read_only": True},
            "hass_entry_id": {"read_only": True},
            "created_at": {"read_only": True},
            "updated_at": {"read_only": True},
        }

    def update(self, instance, validated_data):
        config_data = validated_data.pop("alarm_device", None)
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

            alarm_settings = AlarmSettings.objects.get(device=config.device)
            if config.alarm_mode != AlarmMode.OFF.value:
                AlarmSettings.objects.update_instance(
                    alarm_settings,
                    **{"mode": config.alarm_mode, "sound": AlarmSound.ALARM.value}
                )
            if config.occupancy_illusion != OccupancyIllusion.OFF.value:
                sound = OCCUPANCY_TO_SOUND_MAP.get(config.occupancy_illusion)

                if sound:
                    AlarmSettings.objects.update_instance(
                        alarm_settings,
                        **{"mode": AlarmSettingsMode.TRAVEL.value, "sound": sound}
                    )

            if (
                config.occupancy_illusion == AlarmMode.OFF.value
                and config.alarm_mode == AlarmMode.OFF.value
            ):
                AlarmSettings.objects.update_instance(
                    alarm_settings, **{"mode": AlarmSettingsMode.NONE.value}
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
            "type": {
                "read_only": True,
            },
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
