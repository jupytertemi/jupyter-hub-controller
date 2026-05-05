from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("camera", "0024_backfill_camera_ip_from_rtsp_url"),
    ]

    operations = [
        migrations.AddField(
            model_name="camera",
            name="main_stream_width",
            field=models.PositiveIntegerField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name="camera",
            name="main_stream_height",
            field=models.PositiveIntegerField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name="camera",
            name="sub_stream_width",
            field=models.PositiveIntegerField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name="camera",
            name="sub_stream_height",
            field=models.PositiveIntegerField(null=True, blank=True),
        ),
    ]
