from django.db import migrations
from pgvector.django import VectorExtension


class Migration(migrations.Migration):
    dependencies = [
        ("face_training", "0001_initial"),
    ]

    operations = [VectorExtension()]
