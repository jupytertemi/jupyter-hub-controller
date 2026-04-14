from rest_framework import serializers

from vehicle.models import Vehicle


class VehicleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vehicle
        fields = "__all__"
        extra_kwargs = {
            "id": {"read_only": True},
        }
