from rest_framework import serializers


class LoiteringSerializer(serializers.Serializer):
    restrictive_zone = serializers.BooleanField(default=False)
