from rest_framework import serializers

from external_device.models import ExternalDevice


class ExternalDeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExternalDevice
        fields = "__all__"
        extra_kwargs = {
            "id": {"read_only": True},
            "status": {"read_only": True},
            "socket_response": {"read_only": True},
        }


class ExternalDeviceClearSerializer(serializers.Serializer):
    type = serializers.CharField()
