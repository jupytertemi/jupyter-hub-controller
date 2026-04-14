from rest_framework import serializers

from automation_garage.models import GarageDoorSettings


class GarageDoorSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = GarageDoorSettings
        fields = "__all__"
        extra_kwargs = {
            "id": {"read_only": True},
        }

    def update(self, instance, validated_data):
        return GarageDoorSettings.objects.update_instance(instance, **validated_data)
