from enum import Enum
from pathlib import Path

from django.conf import settings
from rest_framework import serializers


class TransferType(Enum):
    SEND = "send"
    RECEIVE = "receive"

    @classmethod
    def choices(cls):
        return [(key.value, key.name) for key in cls]


class CreateTransferSessionSerializer(serializers.Serializer):
    file_url = serializers.CharField()
    session_id = serializers.CharField()
    type = serializers.ChoiceField(choices=TransferType.choices())
    ice_servers = serializers.JSONField(default=[])

    def validate_file_url(self, value):
        transfer_type = self.initial_data.get("type")
        if transfer_type == TransferType.RECEIVE.value:
            prefix = "http://frigate:5000/api/events"
            if not value.startswith(prefix):
                raise serializers.ValidationError(
                    f"The value must start with '{prefix}'"
                )
            # Change the prefix from the value
            value = value.replace(prefix, "http://localhost/frigate/events")
        else:
            value = Path(settings.RECEIVING_FILE_DIR) / value
        return value


class CreatePlayBackSessionSerializer(CreateTransferSessionSerializer):
    type = None

    def validate_file_url(self, value):
        if value.startswith(("http://", "https://")):
            prefix = "http://frigate:5000/api/events"
            if not value.startswith(prefix):
                raise serializers.ValidationError(
                    f"The value must start with '{prefix}'"
                )

            value = value.replace(
                prefix,
                f"http://{settings.MQTT_USERNAME}:{settings.FRIGATE_PASSWORD}@localhost/frigate/events",
            )
        else:
            prefix = "media/frigate/"
            if not value.startswith(prefix):
                raise serializers.ValidationError(
                    f"The value must start with '{prefix}'"
                )

            value = value.replace("/media/frigate", settings.MEDIA_ROOT)
        return value
