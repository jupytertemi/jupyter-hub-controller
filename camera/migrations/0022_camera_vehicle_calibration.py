from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("camera", "0021_camera_onvif_manufacturer_model"),
    ]

    operations = [
        migrations.AddField(
            model_name="camera",
            name="vehicle_entry_point_x",
            field=models.FloatField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name="camera",
            name="vehicle_entry_point_y",
            field=models.FloatField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name="camera",
            name="vehicle_approach_angle_deg",
            field=models.FloatField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name="camera",
            name="vehicle_park_polygon",
            field=models.JSONField(null=True, blank=True),
        ),
    ]
