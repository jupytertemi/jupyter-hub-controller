from rest_framework import serializers

from .models import GoogleDriveAccount, BackupRecord, BackupSchedule


class GoogleDriveAccountSerializer(serializers.ModelSerializer):
    """
    Serializer for Google Drive account status.
    Never exposes tokens -- only connection metadata.
    """

    class Meta:
        model = GoogleDriveAccount
        fields = [
            "id",
            "email",
            "is_active",
            "connected_at",
            "updated_at",
        ]
        read_only_fields = fields


class BackupRecordSerializer(serializers.ModelSerializer):
    """Serializer for backup history listing."""

    size_display = serializers.SerializerMethodField()
    duration_seconds = serializers.SerializerMethodField()

    progress_percent = serializers.SerializerMethodField()

    class Meta:
        model = BackupRecord
        fields = [
            "id",
            "backup_type",
            "status",
            "size_bytes",
            "size_display",
            "gdrive_file_id",
            "gdrive_folder_id",
            "filename",
            "created_at",
            "completed_at",
            "error_message",
            "celery_task_id",
            "duration_seconds",
            "progress_current",
            "progress_total",
            "progress_phase",
            "progress_percent",
        ]
        read_only_fields = fields

    def get_size_display(self, obj):
        """Human-readable file size."""
        size = obj.size_bytes
        if size == 0:
            return "0 B"
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if abs(size) < 1024.0:
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} PB"

    def get_duration_seconds(self, obj):
        """Duration of the backup operation in seconds."""
        if obj.created_at and obj.completed_at:
            delta = obj.completed_at - obj.created_at
            return int(delta.total_seconds())
        return None

    def get_progress_percent(self, obj):
        """Progress as a percentage (0-100), or null if total is unknown."""
        if obj.progress_total and obj.progress_total > 0:
            return round(min(obj.progress_current / obj.progress_total * 100, 100), 1)
        return None


class BackupRecordDetailSerializer(BackupRecordSerializer):
    """Extended detail serializer for individual backup records."""

    class Meta(BackupRecordSerializer.Meta):
        pass


class StartBackupSerializer(serializers.Serializer):
    """Validates input for starting a new backup."""

    backup_type = serializers.ChoiceField(
        choices=BackupRecord.BackupType.choices,
        help_text="Type of backup to create: settings, media, or full",
    )
    media_types = serializers.ListField(
        child=serializers.ChoiceField(choices=["recordings", "clips", "snapshots"]),
        required=False,
        default=None,
        help_text="Which media dirs to include. None = all three.",
    )
    settings_categories = serializers.ListField(
        child=serializers.ChoiceField(choices=[
            "cameras", "camera_settings", "camera_zones",
            "faces", "vehicles",
            "alarm_settings", "garage_door_settings",
            "external_devices", "meross_accounts", "meross_devices",
            "backup_schedules",
        ]),
        required=False,
        default=None,
        help_text="Which settings to include. None = all.",
    )
    cleanup_after = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Delete local media files after successful upload.",
    )


class RestoreBackupSerializer(serializers.Serializer):
    """Validates input for restoring from a backup."""

    confirm = serializers.BooleanField(
        default=False,
        help_text="Must be true to confirm the restore operation",
    )


class BackupScheduleSerializer(serializers.ModelSerializer):
    """Serializer for backup schedule CRUD."""

    class Meta:
        model = BackupSchedule
        fields = [
            "id",
            "backup_type",
            "frequency",
            "time_of_day",
            "day_of_week",
            "day_of_month",
            "is_enabled",
            "last_run",
            "next_run",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "last_run", "next_run", "created_at", "updated_at"]

    def validate_day_of_week(self, value):
        if value is not None and not (0 <= value <= 6):
            raise serializers.ValidationError(
                "day_of_week must be between 0 (Monday) and 6 (Sunday)."
            )
        return value

    def validate_day_of_month(self, value):
        if value is not None and not (1 <= value <= 28):
            raise serializers.ValidationError(
                "day_of_month must be between 1 and 28."
            )
        return value

    def validate(self, data):
        frequency = data.get("frequency", getattr(self.instance, "frequency", None))

        if frequency == BackupSchedule.Frequency.WEEKLY:
            day_of_week = data.get(
                "day_of_week",
                getattr(self.instance, "day_of_week", None),
            )
            if day_of_week is None:
                raise serializers.ValidationError(
                    {"day_of_week": "Required for weekly schedules."}
                )

        if frequency == BackupSchedule.Frequency.MONTHLY:
            day_of_month = data.get(
                "day_of_month",
                getattr(self.instance, "day_of_month", None),
            )
            if day_of_month is None:
                raise serializers.ValidationError(
                    {"day_of_month": "Required for monthly schedules."}
                )

        return data

    def create(self, validated_data):
        schedule = super().create(validated_data)
        schedule.compute_next_run()
        schedule.save(update_fields=["next_run"])
        return schedule

    def update(self, instance, validated_data):
        instance = super().update(instance, validated_data)
        instance.compute_next_run()
        instance.save(update_fields=["next_run"])
        return instance


class DriveSpaceSerializer(serializers.Serializer):
    """Read-only serializer for Google Drive storage info."""

    total_bytes = serializers.IntegerField()
    used_bytes = serializers.IntegerField()
    available_bytes = serializers.IntegerField()
    total_display = serializers.CharField()
    used_display = serializers.CharField()
    available_display = serializers.CharField()
    usage_percent = serializers.FloatField()
    estimated_settings_backup_mb = serializers.FloatField()
    estimated_media_backup_mb = serializers.FloatField()
    estimated_recordings_mb = serializers.FloatField()
    estimated_clips_mb = serializers.FloatField()
    estimated_snapshots_mb = serializers.FloatField()
