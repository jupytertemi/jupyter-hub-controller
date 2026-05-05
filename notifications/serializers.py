import re

from rest_framework import serializers

# APNs raw device token: 64-char hex (legacy iOS) or 64-200 char base64-ish.
# Be permissive on length to survive future Apple format changes.
_APNS_TOKEN_RE = re.compile(r"^[A-Za-z0-9+/=_-]{32,200}$")


class APNsTokenRegisterSerializer(serializers.Serializer):
    device_token = serializers.CharField(min_length=32, max_length=200)
    device_id = serializers.CharField(min_length=4, max_length=128)
    bundle_id = serializers.CharField(max_length=128)
    environment = serializers.ChoiceField(choices=["sandbox", "production"])
    platform = serializers.ChoiceField(choices=["ios", "android"], default="ios")

    def validate_device_token(self, value):
        if not _APNS_TOKEN_RE.match(value):
            raise serializers.ValidationError("invalid APNs device token format")
        return value

    def validate_device_id(self, value):
        # Reject obviously-bad inputs without being overly strict — device_id
        # comes from the client (UUID, IDFV, or a generated nonce).
        if not value.strip():
            raise serializers.ValidationError("device_id cannot be empty")
        return value.strip()
