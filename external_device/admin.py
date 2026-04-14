from django.contrib import admin

from external_device.models import ExternalDevice


@admin.register(ExternalDevice)
class ExternalDeviceAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "mac_address",
        "name",
        "type",
    )
