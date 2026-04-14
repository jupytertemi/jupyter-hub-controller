from django.db import models


class CameraManufacturer(models.Model):
    manufacturer_name = models.CharField(max_length=256, null=True)

    class Meta:
        indexes = [
            models.Index(fields=["manufacturer_name"]),
        ]


class CameraModel(models.Model):
    camera_manufacturer = models.ForeignKey(
        CameraManufacturer,
        on_delete=models.CASCADE,
        related_name="camera_model_camera_manufacturer",
        null=True,
        blank=True,
    )
    model = models.CharField(max_length=256, null=True)
    type = models.CharField(max_length=256, null=True)
    protocol = models.CharField(max_length=256, null=True)
    url = models.CharField(max_length=256, null=True)

    class Meta:
        indexes = [
            models.Index(fields=["model"]),
            models.Index(fields=["type"]),
            models.Index(fields=["camera_manufacturer", "model"]),
        ]
