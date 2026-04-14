import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="GoogleDriveAccount",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "email",
                    models.EmailField(
                        max_length=254,
                        unique=True,
                        help_text="Google account email address",
                    ),
                ),
                (
                    "access_token",
                    models.TextField(
                        db_column="access_token",
                        help_text="Encrypted OAuth2 access token",
                    ),
                ),
                (
                    "refresh_token",
                    models.TextField(
                        db_column="refresh_token",
                        help_text="Encrypted OAuth2 refresh token",
                    ),
                ),
                (
                    "token_expiry",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                        help_text="When the access token expires",
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(
                        default=True,
                        help_text="Whether this account is currently connected and active",
                    ),
                ),
                (
                    "connected_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        help_text="When the account was first connected",
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True,
                        help_text="Last time tokens were refreshed or account was modified",
                    ),
                ),
            ],
            options={
                "verbose_name": "Google Drive Account",
                "verbose_name_plural": "Google Drive Accounts",
                "ordering": ["-connected_at"],
            },
        ),
        migrations.CreateModel(
            name="BackupRecord",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "backup_type",
                    models.CharField(
                        choices=[
                            ("settings", "Settings"),
                            ("media", "Media"),
                            ("full", "Full"),
                        ],
                        max_length=10,
                        help_text="Type of backup: settings, media, or full",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("running", "Running"),
                            ("completed", "Completed"),
                            ("failed", "Failed"),
                        ],
                        default="pending",
                        max_length=10,
                        help_text="Current status of the backup operation",
                    ),
                ),
                (
                    "size_bytes",
                    models.BigIntegerField(
                        default=0,
                        help_text="Size of the backup file in bytes",
                    ),
                ),
                (
                    "gdrive_file_id",
                    models.CharField(
                        blank=True,
                        default="",
                        max_length=255,
                        help_text="Google Drive file ID for the uploaded backup",
                    ),
                ),
                (
                    "gdrive_folder_id",
                    models.CharField(
                        blank=True,
                        default="",
                        max_length=255,
                        help_text="Google Drive folder ID where the backup is stored",
                    ),
                ),
                (
                    "filename",
                    models.CharField(
                        blank=True,
                        default="",
                        max_length=255,
                        help_text="Name of the backup file in Google Drive",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        help_text="When the backup was initiated",
                    ),
                ),
                (
                    "completed_at",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                        help_text="When the backup completed (success or failure)",
                    ),
                ),
                (
                    "error_message",
                    models.TextField(
                        blank=True,
                        default="",
                        help_text="Error message if the backup failed",
                    ),
                ),
                (
                    "celery_task_id",
                    models.CharField(
                        blank=True,
                        default="",
                        max_length=255,
                        help_text="Celery task ID for tracking async operations",
                    ),
                ),
            ],
            options={
                "verbose_name": "Backup Record",
                "verbose_name_plural": "Backup Records",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="BackupSchedule",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "backup_type",
                    models.CharField(
                        choices=[
                            ("settings", "Settings"),
                            ("media", "Media"),
                            ("full", "Full"),
                        ],
                        max_length=10,
                        help_text="Type of backup to run on schedule",
                    ),
                ),
                (
                    "frequency",
                    models.CharField(
                        choices=[
                            ("daily", "Daily"),
                            ("weekly", "Weekly"),
                            ("monthly", "Monthly"),
                        ],
                        max_length=10,
                        help_text="How often to run the backup",
                    ),
                ),
                (
                    "time_of_day",
                    models.TimeField(
                        help_text="Time of day to run the backup (HH:MM)",
                    ),
                ),
                (
                    "day_of_week",
                    models.IntegerField(
                        blank=True,
                        null=True,
                        help_text="Day of week for weekly backups (0=Monday, 6=Sunday)",
                    ),
                ),
                (
                    "day_of_month",
                    models.IntegerField(
                        blank=True,
                        null=True,
                        help_text="Day of month for monthly backups (1-28)",
                    ),
                ),
                (
                    "is_enabled",
                    models.BooleanField(
                        default=True,
                        help_text="Whether this schedule is active",
                    ),
                ),
                (
                    "last_run",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                        help_text="When this schedule last triggered a backup",
                    ),
                ),
                (
                    "next_run",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                        help_text="Computed next run time",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True),
                ),
            ],
            options={
                "verbose_name": "Backup Schedule",
                "verbose_name_plural": "Backup Schedules",
                "ordering": ["time_of_day"],
            },
        ),
    ]
