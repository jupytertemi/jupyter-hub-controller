from django.db import migrations
from pgvector.django import VectorExtension


class Migration(migrations.Migration):
    dependencies = [
        ("event", "0007_alter_event_event_id"),
    ]

    operations = [VectorExtension()]
