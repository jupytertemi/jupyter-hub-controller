import logging

from django.db import models
from django.core import signing
from django.utils import timezone

logger = logging.getLogger(__name__)


class GoogleDriveAccount(models.Model):
    """
    Stores Google OAuth2 credentials for Google Drive access.
    Tokens are encrypted at rest using Django's signing framework.
    Only one active account is supported per hub.
    """

    email = models.EmailField(unique=True, help_text="Google account email address")
    _access_token = models.TextField(
        db_column="access_token",
        help_text="Encrypted OAuth2 access token",
    )
    _refresh_token = models.TextField(
        db_column="refresh_token",
        help_text="Encrypted OAuth2 refresh token",
    )
    token_expiry = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the access token expires",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this account is currently connected and active",
    )
    connected_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When the account was first connected",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="Last time tokens were refreshed or account was modified",
    )

    class Meta:
        verbose_name = "Google Drive Account"
        verbose_name_plural = "Google Drive Accounts"
        ordering = ["-connected_at"]

    def __str__(self):
        status = "active" if self.is_active else "disconnected"
        return f"{self.email} ({status})"

    @property
    def access_token(self):
        """Decrypt and return the access token."""
        if not self._access_token:
            return None
        try:
            return signing.loads(self._access_token)
        except signing.BadSignature:
            logger.error("Failed to decrypt access token - possible key rotation")
            return None

    @access_token.setter
    def access_token(self, value):
        """Encrypt and store the access token."""
        if value is None:
            self._access_token = ""
        else:
            self._access_token = signing.dumps(value)

    @property
    def refresh_token(self):
        """Decrypt and return the refresh token."""
        if not self._refresh_token:
            return None
        try:
            return signing.loads(self._refresh_token)
        except signing.BadSignature:
            logger.error("Failed to decrypt refresh token - possible key rotation")
            return None

    @refresh_token.setter
    def refresh_token(self, value):
        """Encrypt and store the refresh token."""
        if value is None:
            self._refresh_token = ""
        else:
            self._refresh_token = signing.dumps(value)

    @property
    def is_token_expired(self):
        """Check if the access token has expired."""
        if not self.token_expiry:
            return True
        return timezone.now() >= self.token_expiry


class BackupRecord(models.Model):
    """
    Tracks individual backup operations with their status, size, and
    Google Drive file references for later restore or deletion.
    """

    class BackupType(models.TextChoices):
        SETTINGS = "settings", "Settings"
        MEDIA = "media", "Media"
        FULL = "full", "Full"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    backup_type = models.CharField(
        max_length=10,
        choices=BackupType.choices,
        help_text="Type of backup: settings, media, or full",
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
        help_text="Current status of the backup operation",
    )
    size_bytes = models.BigIntegerField(
        default=0,
        help_text="Size of the backup file in bytes",
    )
    gdrive_file_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Google Drive file ID for the uploaded backup",
    )
    gdrive_folder_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Google Drive folder ID where the backup is stored",
    )
    filename = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Name of the backup file in Google Drive",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When the backup was initiated",
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the backup completed (success or failure)",
    )
    error_message = models.TextField(
        blank=True,
        default="",
        help_text="Error message if the backup failed",
    )
    celery_task_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Celery task ID for tracking async operations",
    )
    progress_current = models.IntegerField(
        default=0,
        help_text="Current progress count (files archived, bytes uploaded, etc.)",
    )
    progress_total = models.IntegerField(
        default=0,
        help_text="Total items to process (0 = unknown/indeterminate)",
    )
    progress_phase = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="Current phase: collecting, archiving, uploading, complete",
    )

    class Meta:
        verbose_name = "Backup Record"
        verbose_name_plural = "Backup Records"
        ordering = ["-created_at"]

    def __str__(self):
        size_mb = self.size_bytes / (1024 * 1024) if self.size_bytes else 0
        return (
            f"{self.get_backup_type_display()} backup "
            f"({self.get_status_display()}, {size_mb:.1f} MB) "
            f"- {self.created_at:%Y-%m-%d %H:%M}"
        )

    def mark_running(self):
        """Transition to running state."""
        self.status = self.Status.RUNNING
        self.save(update_fields=["status", "updated_at"] if hasattr(self, "updated_at") else ["status"])

    def update_progress(self, current, total=0, phase=""):
        """Update progress counters without touching other fields."""
        self.progress_current = current
        if total:
            self.progress_total = total
        if phase:
            self.progress_phase = phase
        self.save(update_fields=["progress_current", "progress_total", "progress_phase"])

    def mark_completed(self, size_bytes=0, gdrive_file_id="", gdrive_folder_id="", filename=""):
        """Transition to completed state with file details."""
        self.status = self.Status.COMPLETED
        self.size_bytes = size_bytes
        self.gdrive_file_id = gdrive_file_id
        self.gdrive_folder_id = gdrive_folder_id
        self.filename = filename
        self.completed_at = timezone.now()
        self.save()

    def mark_failed(self, error_message):
        """Transition to failed state with error details."""
        self.status = self.Status.FAILED
        self.error_message = str(error_message)[:5000]
        self.completed_at = timezone.now()
        self.save()


class BackupSchedule(models.Model):
    """
    Defines recurring backup schedules. Celery beat checks these
    records periodically to trigger automated backups.
    """

    class Frequency(models.TextChoices):
        DAILY = "daily", "Daily"
        WEEKLY = "weekly", "Weekly"
        MONTHLY = "monthly", "Monthly"

    backup_type = models.CharField(
        max_length=10,
        choices=BackupRecord.BackupType.choices,
        help_text="Type of backup to run on schedule",
    )
    frequency = models.CharField(
        max_length=10,
        choices=Frequency.choices,
        help_text="How often to run the backup",
    )
    time_of_day = models.TimeField(
        help_text="Time of day to run the backup (HH:MM)",
    )
    day_of_week = models.IntegerField(
        null=True,
        blank=True,
        help_text="Day of week for weekly backups (0=Monday, 6=Sunday)",
    )
    day_of_month = models.IntegerField(
        null=True,
        blank=True,
        help_text="Day of month for monthly backups (1-28)",
    )
    is_enabled = models.BooleanField(
        default=True,
        help_text="Whether this schedule is active",
    )
    last_run = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this schedule last triggered a backup",
    )
    next_run = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Computed next run time",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
    )
    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        verbose_name = "Backup Schedule"
        verbose_name_plural = "Backup Schedules"
        ordering = ["time_of_day"]

    def __str__(self):
        return (
            f"{self.get_backup_type_display()} - "
            f"{self.get_frequency_display()} at {self.time_of_day:%H:%M}"
        )

    def compute_next_run(self):
        """
        Calculate the next run time based on frequency, day, and time settings.
        Updates self.next_run but does NOT save -- caller must save.
        """
        from datetime import timedelta, datetime as dt_cls

        now = timezone.now()
        today = now.date()

        # Build a candidate datetime for today at the scheduled time
        candidate = timezone.make_aware(
            dt_cls.combine(today, self.time_of_day),
            timezone.get_current_timezone(),
        )

        if self.frequency == self.Frequency.DAILY:
            if candidate <= now:
                candidate += timedelta(days=1)

        elif self.frequency == self.Frequency.WEEKLY:
            target_dow = self.day_of_week if self.day_of_week is not None else 0
            current_dow = today.weekday()
            days_ahead = (target_dow - current_dow) % 7
            if days_ahead == 0 and candidate <= now:
                days_ahead = 7
            candidate += timedelta(days=days_ahead)

        elif self.frequency == self.Frequency.MONTHLY:
            target_day = self.day_of_month if self.day_of_month is not None else 1
            target_day = min(target_day, 28)  # Clamp to 28 for safety
            try:
                candidate = candidate.replace(day=target_day)
            except ValueError:
                candidate = candidate.replace(day=28)
            if candidate <= now:
                # Move to next month
                month = candidate.month + 1
                year = candidate.year
                if month > 12:
                    month = 1
                    year += 1
                candidate = candidate.replace(year=year, month=month, day=target_day)

        self.next_run = candidate
        return candidate
