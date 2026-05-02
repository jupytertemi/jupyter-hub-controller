# Generated manually for v1.6 Halo onboard
# Adds:
#   1. AlarmDevice.device_secret column — populated by transfer_server webhook
#      from the TCP register payload. Used by /api/alarms/{slug}/recovery-secret
#      for keychain recovery on a new phone.
#   2. HaDiscoveryState model — tracks last published HA Auto-Discovery
#      fingerprint per AlarmDevice to avoid republishing on register heartbeats.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("alarm", "0007_add_ip_mac_to_alarm_device"),
    ]

    operations = [
        migrations.AddField(
            model_name="alarmdevice",
            name="device_secret",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Halo's firmware-generated 64-hex secret. Sensitive — write-only.",
                max_length=128,
            ),
        ),
        migrations.CreateModel(
            name="HaDiscoveryState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("fingerprint", models.CharField(blank=True, default="", max_length=512)),
                (
                    "device",
                    models.OneToOneField(
                        on_delete=models.deletion.CASCADE,
                        related_name="ha_discovery_state",
                        to="alarm.alarmdevice",
                    ),
                ),
            ],
            options={"abstract": False},
        ),
    ]
