from rest_framework import serializers

from facial.models import Facial


class FacialSerializer(serializers.ModelSerializer):
    video_file = serializers.FileField(write_only=True)
    avatar_file = serializers.FileField(write_only=True)

    class Meta:
        model = Facial
        fields = "__all__"
        extra_kwargs = {
            "id": {"read_only": True},
            "video_url": {"read_only": True},
            "processing": {"read_only": True},
            "embedding": {"read_only": True},
        }
