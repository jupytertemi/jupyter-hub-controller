from asgiref.sync import async_to_sync
from rest_framework import serializers

from ring.models import RingAccount


class RingAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = RingAccount
        fields = "__all__"
        extra_kwargs = {
            "id": {"read_only": True},
        }


class RingAccountLoginSerializer(serializers.Serializer):
    id = serializers.CharField(read_only=True)
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)
    auth_code = serializers.CharField(required=False, write_only=True)

    def create(self, validated_data):
        return async_to_sync(RingAccount.objects.authenticate)(**validated_data)


class RingDeviceListSerializer(serializers.Serializer):
    name = serializers.CharField()
    ring_device_id = serializers.CharField()
    ring_id = serializers.CharField()
