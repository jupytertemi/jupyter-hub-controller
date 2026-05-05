from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("camera", "0025_camera_stream_resolutions"),
    ]

    operations = [
        migrations.AddField(
            model_name="camera",
            name="vehicle_car_outline",
            field=models.JSONField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name="camera",
            name="vehicle_plate_readability_px",
            field=models.FloatField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name="camera",
            name="vehicle_plate_ocr_skip",
            field=models.BooleanField(default=False),
        ),
    ]
