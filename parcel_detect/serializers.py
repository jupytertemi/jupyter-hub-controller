from rest_framework import serializers


class ParcelDetectSerializer(serializers.Serializer):
    camera_name = serializers.CharField(max_length=255, allow_null=True, required=False)
    box = serializers.ListField(child=serializers.IntegerField(), default=[])
