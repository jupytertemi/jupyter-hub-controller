from rest_framework import serializers


class WifiCredentialsSerializer(serializers.Serializer):
    ssid = serializers.CharField()
    password = serializers.CharField()
    mdns = serializers.CharField(allow_null=True) 


class WifiNetworkSerializer(serializers.Serializer):
    ssid = serializers.CharField()
    signal = serializers.IntegerField()
    security = serializers.CharField()
    in_use = serializers.BooleanField(default=False)


class WifiConnectSerializer(serializers.Serializer):
    ssid = serializers.CharField(required=True)
    password = serializers.CharField(required=False, allow_blank=True)