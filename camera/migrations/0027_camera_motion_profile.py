"""MotionIQ — per-camera Frigate sensitivity profile.

Adds Camera.motion_profile (CharField, default "aware"). Existing rows
backfill to "aware" — that profile's threshold:30 / contour_area:25 keeps
existing hubs close to today's stock-Frigate-defaults baseline.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("camera", "0026_camera_car_outline_calibration"),
    ]

    operations = [
        migrations.AddField(
            model_name="camera",
            name="motion_profile",
            field=models.CharField(
                choices=[
                    ("guardian", "Guardian"),
                    ("aware", "Aware"),
                    ("quiet", "Quiet"),
                ],
                default="aware",
                max_length=16,
            ),
        ),
    ]
