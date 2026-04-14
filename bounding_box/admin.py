from django.contrib import admin

from bounding_box.models import BoundingBox


@admin.register(BoundingBox)
class BoundingBoxAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "zone_type",
        "x1",
        "y1",
        "x2",
        "y2",
    )
