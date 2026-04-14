from rest_framework import serializers

from automation.models import AlarmSettings
from external_device.enum import ExternalType
from external_device.models import ExternalDevice


class AlarmSettingsSerializer(serializers.ModelSerializer):
    entry_sensor_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        write_only=True,
    )
    entry_sensors = serializers.SerializerMethodField(read_only=True)

    def validate_volume(self, value):
        if value < 0 or value > 100:
            raise serializers.ValidationError(
                "Volume must be between 0 and 100 inclusive."
            )
        return value

    def validate_run_duration_sound(self, value):
        if value < 0 or value > 30:
            raise serializers.ValidationError(
                "Run Duration sound must be between 0 and 30 inclusive."
            )
        return value

    def validate_entry_door_exit_delay_seconds(self, value):
        if value < 0 or value > 180:
            raise serializers.ValidationError(
                "Exit delay must be between 0 and 180 seconds."
            )
        return value

    def validate_entry_sensor_ids(self, value):
        if not value:
            return value
        existing_ids = set(
            ExternalDevice.objects.filter(
                id__in=value,
                type=ExternalType.S1,
            ).values_list("id", flat=True)
        )
        missing = sorted(set(value) - existing_ids)
        if missing:
            raise serializers.ValidationError(
                f"Invalid entry sensors (must be type S1): {missing}"
            )
        return value

    class Meta:
        model = AlarmSettings
        exclude = ["id", "created_at", "updated_at"]

    def get_entry_sensors(self, obj):
        if obj.entry_door_all_sensors:
            queryset = ExternalDevice.objects.filter(type=ExternalType.S1)
        else:
            queryset = obj.entry_sensors.filter(type=ExternalType.S1)

        return list(queryset.values_list("id", flat=True))

    def update(self, instance, validated_data):
        entry_sensor_ids = validated_data.pop("entry_sensor_ids", None)
        return AlarmSettings.objects.update_instance(
            instance,
            entry_sensor_ids=entry_sensor_ids,
            **validated_data,
        )
