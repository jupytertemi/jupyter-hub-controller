from django.db import models

from vehicle.enums import CategoryType


class Vehicle(models.Model):
    image_url = models.TextField(null=True)
    license_plate = models.CharField(max_length=256, default="")
    owner_name = models.CharField(max_length=256, default="")
    category_type = models.CharField(
        max_length=256,
        choices=CategoryType.choices,
        default=None,
        null=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["license_plate", "owner_name"],
                name="unique_owner_license_plate",
            )
        ]
