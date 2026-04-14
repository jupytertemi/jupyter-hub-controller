from rest_framework import serializers

from cloudflare_turn.models import Turn


class PreviousTurnSerializer(serializers.ModelSerializer):
    class Meta:
        model = Turn
        fields = (
            "credential",
            "created_at",
            "updated_at",
        )


class TurnSerializer(serializers.ModelSerializer):
    previous_turn = PreviousTurnSerializer(read_only=True)

    class Meta:
        model = Turn
        fields = (
            "id",
            "uid",
            "name",
            "credential",
            "previous_turn",
            "created_at",
            "updated_at",
        )
