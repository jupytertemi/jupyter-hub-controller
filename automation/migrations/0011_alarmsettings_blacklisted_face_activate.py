from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("automation", "0010_alarmsettings_entry_door_activate_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="alarmsettings",
            name="blacklisted_face_activate",
            field=models.BooleanField(default=False),
        ),
    ]
