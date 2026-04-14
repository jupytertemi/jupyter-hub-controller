from rest_framework import serializers


class ConfirmSerializer(serializers.Serializer):
    confirm = serializers.BooleanField()
