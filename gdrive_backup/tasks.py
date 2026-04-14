"""
Celery tasks for Google Drive backup operations.

All long-running operations (backup, restore, space checks) run as
asynchronous Celery tasks so the API can return immediately with a
task ID for the Flutter app to poll.
"""

import logging
import os
import shutil
import tempfile

from celery import shared_task
from django.utils import timezone

from .backup_service import _format_bytes

logger = logging.getLogger(__name__)


def _cleanup_media_files(media_types):
    """
    Delete local media files for the given types after a successful backup.
    Removes files individually, then empty subdirectories, but preserves
    the root directories themselves.
    """
    from .backup_service import MediaBackupService
    type_map = MediaBackupService.TYPE_PATH_MAP

    for mt in media_types:
        path = type_map.get(mt)
        if not path or not os.path.isdir(path):
            continue
        logger.info("Cleanup: removing files in %s", path)
        removed = 0
        for dirpath, dirnames, filenames in os.walk(path, topdown=False):
            for fn in filenames:
                fp = os.path.join(dirpath, fn)
                try:
                    os.remove(fp)
                    removed += 1
                except OSError as e:
                    logger.warning("Cleanup: failed to remove %s: %s", fp, e)
            # Remove empty subdirs (but not the root)
            if dirpath != path:
                try:
                    os.rmdir(dirpath)
                except OSError:
                    pass
        logger.info("Cleanup: removed %d files from %s", removed, path)


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def run_backup(self, backup_record_id, backup_type, media_types=None, settings_categories=None, cleanup_after=False, is_scheduled=False):
    """
    Execute a backup operation: collect data, upload to Google Drive,
    and update the BackupRecord with results.

    Args:
        backup_record_id: PK of the BackupRecord to update.
        backup_type: One of 'settings', 'media', 'full'.
        media_types: List of 'recordings', 'clips', 'snapshots' or None for all.
        settings_categories: List of settings categories or None for all.
        cleanup_after: If True, delete local files after successful upload.
        is_scheduled: If True, use incremental backup (only new files since last backup).
    """
    from .models import BackupRecord
    from .gdrive_service import GoogleDriveService, GoogleDriveServiceError
    from .backup_service import (
        SettingsBackupService, MediaBackupService,
        FRIGATE_RECORDINGS_PATH, FRIGATE_CLIPS_PATH, FRIGATE_SNAPSHOTS_PATH,
    )

    try:
        record = BackupRecord.objects.get(pk=backup_record_id)
    except BackupRecord.DoesNotExist:
        logger.error("BackupRecord %s not found", backup_record_id)
        return

    record.status = BackupRecord.Status.RUNNING
    record.celery_task_id = self.request.id or ""
    record.save(update_fields=["status", "celery_task_id"])

    # Calculate total steps for progress tracking:
    # settings collect (1) + settings archive (1) + media count (1) + media archive (1) + upload per file
    total_steps = 0
    if backup_type in ("settings", "full"):
        total_steps += 2  # collect + archive
    if backup_type in ("media", "full"):
        total_steps += 2  # count + archive
    total_steps += 1  # upload phase (at least 1)
    record.update_progress(0, total_steps, "preparing")

    # Use NVMe disk for temp files — /tmp is a 3.9GB tmpfs ramdisk
    # that can't hold large media archives
    nvme_tmp = "/root/jupyter-container/backup_tmp"
    os.makedirs(nvme_tmp, exist_ok=True)
    tmp_dir = tempfile.mkdtemp(prefix="secureprotect_backup_task_", dir=nvme_tmp)
    step = 0

    try:
        gdrive = GoogleDriveService()
        gdrive.load_credentials()
        folder_id = gdrive.get_or_create_backup_folder()

        files_to_upload = []

        # -- Settings --
        if backup_type in ("settings", "full"):
            step += 1
            record.update_progress(step, phase="collecting")
            logger.info("Creating settings backup...")
            svc = SettingsBackupService()
            filepath, filename = svc.create_backup_file(output_dir=tmp_dir, categories=settings_categories)
            files_to_upload.append((filepath, filename))
            step += 1
            record.update_progress(step, phase="archiving")

        # -- Media --
        if backup_type in ("media", "full"):
            step += 1
            record.update_progress(step, phase="counting_files")
            logger.info("Creating media backup...")
            svc = MediaBackupService()

            # Determine incremental cutoff from last successful media backup
            incremental_since = None
            if is_scheduled:
                last_ok = BackupRecord.objects.filter(
                    backup_type__in=["media", "full"],
                    status=BackupRecord.Status.COMPLETED,
                ).order_by("-completed_at").first()
                if last_ok and last_ok.completed_at:
                    incremental_since = last_ok.completed_at
                    logger.info("Incremental backup since %s", incremental_since.isoformat())

            filepath, filename, file_count = svc.create_backup_file(
                output_dir=tmp_dir,
                media_types=media_types,
                incremental_since=incremental_since,
            )

            if file_count == 0 and incremental_since:
                logger.info("No new files since last backup — skipping upload")
                record.mark_completed(size_bytes=0, filename="(no new files)")
                return

            files_to_upload.append((filepath, filename))
            step += 1
            record.update_progress(step, phase="archiving")

        # Recalculate total with actual upload count
        upload_steps = len(files_to_upload)
        record.update_progress(step, step + upload_steps, "uploading")

        # Upload all files
        total_size = 0
        last_file_id = ""

        for i, (filepath, filename) in enumerate(files_to_upload):
            file_size = os.path.getsize(filepath)
            record.update_progress(
                step + i,
                step + upload_steps,
                f"uploading {_format_bytes(file_size)}",
            )
            logger.info("Uploading %s to Google Drive...", filename)
            result = gdrive.upload_file(filepath, filename, folder_id)
            total_size += result["size"]
            last_file_id = result["file_id"]

        record.update_progress(step + upload_steps, step + upload_steps, "complete")
        record.mark_completed(
            size_bytes=total_size,
            gdrive_file_id=last_file_id,
            gdrive_folder_id=folder_id,
            filename=files_to_upload[-1][1] if files_to_upload else "",
        )
        logger.info(
            "Backup %s completed successfully: %d bytes uploaded",
            backup_record_id,
            total_size,
        )

        # Cleanup local files if requested
        if cleanup_after and backup_type in ("media", "full"):
            cleanup_types = media_types or ["recordings", "clips", "snapshots"]
            logger.info("Cleanup requested for types: %s", cleanup_types)
            try:
                _cleanup_media_files(cleanup_types)
            except Exception as e:
                logger.error("Cleanup failed (backup still succeeded): %s", e)

    except GoogleDriveServiceError as e:
        logger.error("Google Drive error during backup %s: %s", backup_record_id, e)
        record.mark_failed(str(e))
        # Retry on transient Google API errors
        if "token" not in str(e).lower():
            raise self.retry(exc=e)

    except Exception as e:
        logger.error("Backup %s failed: %s", backup_record_id, e, exc_info=True)
        record.mark_failed(str(e))

    finally:
        # Clean up temp files
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


@shared_task(bind=True, max_retries=1, default_retry_delay=30)
def run_restore(self, backup_record_id):
    """
    Download a backup from Google Drive and restore it locally.

    Args:
        backup_record_id: PK of the BackupRecord to restore from.
    """
    from .models import BackupRecord
    from .gdrive_service import GoogleDriveService, GoogleDriveServiceError
    from .backup_service import SettingsRestoreService, MediaRestoreService

    try:
        record = BackupRecord.objects.get(pk=backup_record_id)
    except BackupRecord.DoesNotExist:
        logger.error("BackupRecord %s not found", backup_record_id)
        return {"status": "error", "message": "Backup record not found"}

    if not record.gdrive_file_id:
        logger.error("BackupRecord %s has no Google Drive file ID", backup_record_id)
        return {"status": "error", "message": "No Google Drive file ID"}

    tmp_dir = tempfile.mkdtemp(prefix="secureprotect_restore_task_")

    try:
        gdrive = GoogleDriveService()
        gdrive.load_credentials()

        # Download the backup file
        destination = os.path.join(tmp_dir, record.filename or "backup_file")
        logger.info("Downloading backup file %s...", record.gdrive_file_id)
        gdrive.download_file(record.gdrive_file_id, destination)

        # Determine restore type from filename or backup_type
        summary = {}

        if record.backup_type in ("settings", "full"):
            if record.filename and "settings" in record.filename:
                logger.info("Restoring settings from %s", destination)
                restore_svc = SettingsRestoreService()
                summary["settings"] = restore_svc.restore_from_file(destination)

        if record.backup_type in ("media", "full"):
            if record.filename and "media" in record.filename:
                logger.info("Restoring media from %s", destination)
                restore_svc = MediaRestoreService()
                summary["media"] = restore_svc.restore_from_file(destination)

        # For full backups where both settings and media are in the folder,
        # we need to download both files
        if record.backup_type == "full" and not summary:
            # Try to restore as settings first (most common single-file case)
            try:
                restore_svc = SettingsRestoreService()
                summary["settings"] = restore_svc.restore_from_file(destination)
            except Exception:
                pass

            if not summary:
                try:
                    restore_svc = MediaRestoreService()
                    summary["media"] = restore_svc.restore_from_file(destination)
                except Exception:
                    pass

        logger.info("Restore complete for backup %s: %s", backup_record_id, summary)
        return {"status": "success", "summary": summary}

    except GoogleDriveServiceError as e:
        logger.error("Google Drive error during restore: %s", e)
        return {"status": "error", "message": str(e)}

    except Exception as e:
        logger.error("Restore failed: %s", e, exc_info=True)
        return {"status": "error", "message": str(e)}

    finally:
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


@shared_task
def run_scheduled_backups():
    """
    Celery beat task: check all enabled backup schedules and trigger
    any that are due. Designed to run every 5 minutes via Celery beat.

    Behaviour:
    - If the hub was off and a schedule was missed, next_run is in the
      past, so it fires as soon as the hub boots and this task runs.
    - If another backup (scheduled or manual) is already running, this
      schedule queues behind it (PENDING) instead of being skipped.
    - Celery's single-worker concurrency ensures only one backup task
      runs at a time; the queued one starts when the running one finishes.
    """
    from .models import BackupSchedule, BackupRecord, GoogleDriveAccount

    # Bail early if no Google account is connected
    if not GoogleDriveAccount.objects.filter(is_active=True).exists():
        logger.debug("No active Google Drive account, skipping scheduled backups")
        return

    now = timezone.now()
    due_schedules = BackupSchedule.objects.filter(
        is_enabled=True,
        next_run__lte=now,
    )

    if not due_schedules.exists():
        return

    for schedule in due_schedules:
        logger.info(
            "Triggering scheduled %s backup (schedule id=%d)",
            schedule.backup_type,
            schedule.pk,
        )

        # Check if THIS schedule already has a pending/running record
        # (prevents duplicate dispatches if beat fires twice before task starts)
        already_queued = BackupRecord.objects.filter(
            backup_type=schedule.backup_type,
            status__in=[BackupRecord.Status.PENDING, BackupRecord.Status.RUNNING],
        ).exists()

        if already_queued:
            logger.info(
                "Scheduled %s backup already queued/running — advancing schedule without duplicate",
                schedule.backup_type,
            )
        else:
            # Create a new backup record and dispatch.
            # If another backup type is running, this sits in the Celery queue
            # and starts automatically when the worker is free.
            record = BackupRecord.objects.create(
                backup_type=schedule.backup_type,
                status=BackupRecord.Status.PENDING,
            )
            run_backup.delay(record.pk, schedule.backup_type, is_scheduled=True)

        schedule.last_run = now
        schedule.compute_next_run()
        schedule.save(update_fields=["last_run", "next_run"])

    logger.info("Scheduled backup check complete. Processed %d schedules.", due_schedules.count())


@shared_task
def check_gdrive_space():
    """
    Periodic task to check Google Drive storage and log warnings
    if the account is running low. Runs every 6 hours.
    """
    from .models import GoogleDriveAccount
    from .gdrive_service import GoogleDriveService, GoogleDriveServiceError

    if not GoogleDriveAccount.objects.filter(is_active=True).exists():
        return

    try:
        gdrive = GoogleDriveService()
        gdrive.load_credentials()
        quota = gdrive.get_storage_quota()

        usage_pct = quota.get("usage_percent", 0)
        available = quota.get("available_display", "unknown")

        if usage_pct >= 95:
            logger.warning(
                "CRITICAL: Google Drive storage at %.1f%% (%s available). "
                "Backups may fail.",
                usage_pct,
                available,
            )
        elif usage_pct >= 85:
            logger.warning(
                "Google Drive storage at %.1f%% (%s available). "
                "Consider freeing space or upgrading.",
                usage_pct,
                available,
            )
        else:
            logger.info(
                "Google Drive storage: %.1f%% used (%s available)",
                usage_pct,
                available,
            )

    except GoogleDriveServiceError as e:
        logger.error("Failed to check Google Drive space: %s", e)
    except Exception as e:
        logger.error("Unexpected error checking Google Drive space: %s", e)
