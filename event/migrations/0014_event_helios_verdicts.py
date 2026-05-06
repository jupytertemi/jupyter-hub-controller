"""Helios Tier 1 §3.1 — forensic verdicts on Event.

Adds 4 nullable fields + 2 indexes. Pre-existing rows read as
verdict=null which Helios renders as "no verdict yet" (the default
chip state). Index on verdict + verdict_at supports the dashboard's
"unresolved verdicts in last 7 days" filter without a table scan.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("event", "0013_hnsw_embedding_index"),
    ]

    operations = [
        migrations.AddField(
            model_name="event",
            name="verdict",
            field=models.CharField(
                max_length=16,
                null=True,
                blank=True,
                choices=[
                    ("resolved", "Resolved"),
                    ("watch", "Watch"),
                    ("false_alarm", "False alarm"),
                ],
            ),
        ),
        migrations.AddField(
            model_name="event",
            name="verdict_note",
            field=models.TextField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name="event",
            name="verdict_by_name",
            field=models.CharField(max_length=120, null=True, blank=True),
        ),
        migrations.AddField(
            model_name="event",
            name="verdict_at",
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddIndex(
            model_name="event",
            index=models.Index(fields=["verdict"], name="event_event_verdict_idx"),
        ),
        migrations.AddIndex(
            model_name="event",
            index=models.Index(fields=["verdict_at"], name="event_event_verdict_at_idx"),
        ),
    ]
