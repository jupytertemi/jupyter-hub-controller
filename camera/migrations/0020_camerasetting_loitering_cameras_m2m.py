from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("camera", "0019_camera_health_watchdog"),
    ]

    operations = [
        migrations.AddField(
            model_name="camerasetting",
            name="loitering_cameras",
            field=models.ManyToManyField(
                blank=True,
                related_name="loitering_cameras_setting",
                to="camera.camera",
            ),
        ),
    ]
