from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("external_device", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="externaldevice",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("success", "Success"),
                    ("failed", "Failed"),
                ],
                default="pending",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="externaldevice",
            name="socket_response",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
