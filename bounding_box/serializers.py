from rest_framework import serializers

from bounding_box.models import BoundingBox


class BoundingBoxSerializer(serializers.ModelSerializer):
    class Meta:
        model = BoundingBox
        fields = "__all__"
        extra_kwargs = {
            "id": {"read_only": True},
        }
