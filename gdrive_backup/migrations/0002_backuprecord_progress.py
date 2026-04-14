from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("gdrive_backup", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="backuprecord",
            name="progress_current",
            field=models.IntegerField(
                default=0,
                help_text="Current progress count (files archived, bytes uploaded, etc.)",
            ),
        ),
        migrations.AddField(
            model_name="backuprecord",
            name="progress_total",
            field=models.IntegerField(
                default=0,
                help_text="Total items to process (0 = unknown/indeterminate)",
            ),
        ),
        migrations.AddField(
            model_name="backuprecord",
            name="progress_phase",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Current phase: collecting, archiving, uploading, complete",
                max_length=50,
            ),
        ),
    ]
