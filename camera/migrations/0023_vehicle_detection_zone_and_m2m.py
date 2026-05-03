from django.db import migrations, models


class Migration(migrations.Migration):
    """Vehicle AI zones redesign — minimal additive migration.

    Adds the foundation detection zone (4-point quad) on Camera and the
    multi-camera M2M on CameraSetting (mirroring loitering_cameras). Legacy
    fields (vehicle_entry_point_x/y, vehicle_approach_angle_deg,
    vehicle_park_polygon, vehicle_recognition_camera ForeignKey) stay
    untouched; the new wizard adds detection_zone before the existing
    arrow + park-rectangle steps.
    """

    dependencies = [
        ("camera", "0022_camera_vehicle_calibration"),
    ]

    operations = [
        migrations.AddField(
            model_name="camera",
            name="vehicle_detection_zone",
            field=models.JSONField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name="camerasetting",
            name="vehicle_recognition_cameras",
            field=models.ManyToManyField(
                blank=True,
                related_name="vehicle_recognition_cameras_setting",
                to="camera.camera",
            ),
        ),
    ]
