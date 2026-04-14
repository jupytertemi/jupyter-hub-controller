import base64

from django.conf import settings
from rest_framework.permissions import BasePermission


def generate_basic_token(username, password):
    credentials = f"{username}:{password}"

    encoded_credentials = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")

    basic_token = f"Basic {encoded_credentials}"
    return basic_token


class HasFRVApiKey(BasePermission):
    def has_permission(self, request, view):
        api_key = request.headers.get("X-API-KEY")
        return api_key == settings.FRV_API_KEY
