# Generated manually for IP/MAC tracking
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('alarm', '0006_alarmdevice_version_fw'),
    ]

    operations = [
        migrations.AddField(
            model_name='alarmdevice',
            name='ip_address',
            field=models.GenericIPAddressField(blank=True, help_text='Current IP address of the alarm device', null=True),
        ),
        migrations.AddField(
            model_name='alarmdevice',
            name='mac_address',
            field=models.CharField(blank=True, default='', help_text='MAC address in format aa:bb:cc:dd:ee:ff', max_length=17),
        ),
    ]
