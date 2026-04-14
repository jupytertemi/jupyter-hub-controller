import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Turn",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("id", models.AutoField(primary_key=True, serialize=False)),
                ("uid", models.CharField(db_index=True, max_length=64)),
                ("name", models.CharField(max_length=255)),
                ("credential", models.JSONField()),
                ("previous_turn", models.JSONField(null=True)),
            ],
            options={
                "abstract": False,
            },
        ),
    ]
