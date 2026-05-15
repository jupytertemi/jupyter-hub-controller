from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("suggested_facial", "0002_suggestedfacial_confidence_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="suggestedfacial",
            name="is_blacklisted",
            field=models.BooleanField(default=False),
        ),
    ]
