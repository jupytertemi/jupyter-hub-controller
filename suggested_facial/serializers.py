from rest_framework import serializers

from suggested_facial.models import SuggestedFacial


class SuggestedFacialSerializer(serializers.ModelSerializer):
    class Meta:
        model = SuggestedFacial
        fields = "__all__"
        extra_kwargs = {
            "id": {"read_only": True},
            "face_embeddings": {"read_only": True},
            "confidence": {"read_only": True},
            "face_embeddings_2": {"read_only": True},
            "confidence_2": {"read_only": True},
            "face_embeddings_3": {"read_only": True},
            "confidence_3": {"read_only": True},
            "person": {"read_only": True},
            "total_times": {"read_only": True},
            "distinct_days": {"read_only": True},
            "title": {"read_only": True},
            "is_almost": {"read_only": True},
        }
