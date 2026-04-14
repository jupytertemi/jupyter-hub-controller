from django.contrib import admin

from camera.models import RTSPCamera


@admin.register(RTSPCamera)
class RTSPCameraAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "username",
        "password",
        "ip",
        "created_at",
        "updated_at",
    )
