from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("camera", "0020_camerasetting_loitering_cameras_m2m"),
    ]

    operations = [
        migrations.AddField(
            model_name="camera",
            name="onvif_manufacturer",
            field=models.CharField(max_length=256, null=True, blank=True),
        ),
        migrations.AddField(
            model_name="camera",
            name="onvif_model",
            field=models.CharField(max_length=256, null=True, blank=True),
        ),
    ]
