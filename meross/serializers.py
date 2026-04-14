from enum import Enum

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from rest_framework import serializers
from rest_framework import status
from rest_framework.exceptions import APIException

from automation_garage.models import GarageDoorSettings
from meross.models import MerossCloudAccount, MerossDevice
from utils.exceptions import CustomException


class MerossValidationException(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "invalid_input"

    def __init__(self, detail):
        self.detail = {"detail": str(detail)}


class MerossDeviceSerializer(serializers.ModelSerializer):
    flow_id = serializers.CharField(write_only=True)

    class Meta:
        model = MerossDevice
        fields = "__all__"
        extra_kwargs = {
            "id": {"read_only": True},
            "name": {"required": True},
            "hass_entry_id": {"read_only": True},
            "created_at": {"read_only": True},
            "updated_at": {"read_only": True},
        }


class MerossCloudSettingSerializer(serializers.ModelSerializer):
    email = serializers.CharField()
    password = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
    )
    save_password = serializers.BooleanField(write_only=True, default=False)
    allow_mqtt_publish = serializers.BooleanField(write_only=True, default=False)
    check_firmware_updates = serializers.BooleanField(write_only=True, default=False)

    class Meta:
        model = MerossCloudAccount
        fields = "__all__"
        extra_kwargs = {
            "id": {"read_only": True},
            "created_at": {"read_only": True},
            "updated_at": {"read_only": True},
        }

    def validate(self, attrs):
        if self.instance is None:
            meross_cloud = MerossCloudAccount.objects.filter().count()
            if meross_cloud >= int(settings.MEROSS_CLOUD_PER):
                raise CustomException("Can not add new Meross Cloud Account.")
        email = (attrs.get("email") or "").strip()
        password = attrs.get("password") or ""
        try:
            validate_email(email)
        except DjangoValidationError as exc:
            raise MerossValidationException("Enter a valid email address.") from exc
        if not password.strip():
            raise MerossValidationException("Password can not be empty.")
        if len(password) < 6:
            raise MerossValidationException("Password must be at least 6 characters.")
        attrs["email"] = email
        validated_data = super().validate(attrs)
        return validated_data


class MerossCloudAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = MerossCloudAccount
        fields = "__all__"


class UpdateMerossDeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = MerossDevice
        fields = ["name"]

    def destroy(self, instance, validated_data):
        return GarageDoorSettings.objects.delete_instance(instance, **validated_data)


class SendMessagesWebSocketSerializer(serializers.Serializer):
    message = serializers.JSONField()


class MerrosGereraDoorStates(Enum):
    CLOSING = "closing"
    OPEN = "open"

    @classmethod
    def choices(cls):
        return [(key.value, key.name) for key in cls]


class GetStatesEntitySerializer(serializers.Serializer):
    states = serializers.ChoiceField(choices=MerrosGereraDoorStates.choices())
