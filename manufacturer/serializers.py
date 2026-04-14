from rest_framework import serializers

from manufacturer.models import CameraManufacturer, CameraModel


class CameraManufacturerSerializer(serializers.ModelSerializer):
    class Meta:
        model = CameraManufacturer
        fields = "__all__"


class CameraModelSerializer(serializers.ModelSerializer):
    class Meta:
        model = CameraModel
        fields = "__all__"
