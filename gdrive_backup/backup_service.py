"""
Backup and restore logic for SecureProtect Hub.

Collects hub configuration and media from the local system, packages it
into compressed archives, and coordinates upload/download with the
Google Drive service layer.
"""

import gzip
import json
import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime

from django.apps import apps
from django.db import connection
from django.utils import timezone

logger = logging.getLogger(__name__)

# Paths on the hub filesystem
FRIGATE_STORAGE_BASE = "/root/jupyter-container/frigate/storage"
FRIGATE_RECORDINGS_PATH = os.path.join(FRIGATE_STORAGE_BASE, "recordings")
FRIGATE_CLIPS_PATH = os.path.join(FRIGATE_STORAGE_BASE, "clips")
FRIGATE_SNAPSHOTS_PATH = os.path.join(FRIGATE_STORAGE_BASE, "snapshots")

# Backup version -- bump when the schema changes so restore can handle
# migration between versions.
BACKUP_VERSION = "1.0.0"


def _get_hub_id():
    """Return the device/hub identifier."""
    try:
        from utils.update_env import read_env_file
        hub_id = read_env_file("DEVICE_NAME")
        if hub_id and hub_id.strip():
            return hub_id.strip()
    except Exception:
        pass
    return "default-hub"


def _format_bytes(size_bytes):
    """Human-readable byte size."""
    if size_bytes == 0:
        return "0 B"
    size = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(size) < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"


def _safe_query(sql, params=None):
    """
    Run a raw SQL query and return a list of dicts.
    Returns an empty list on any error so that a missing table
    never aborts an entire backup.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            columns = [col[0] for col in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
    except Exception as e:
        logger.warning("Query failed (table may not exist): %s -- %s", sql[:80], e)
        return []


def _safe_model_export(app_label, model_name, fields=None):
    """
    Export all rows from a Django model as a list of dicts.
    Gracefully returns [] if the model or table is missing.
    """
    try:
        Model = apps.get_model(app_label, model_name)
        qs = Model.objects.all()
        if fields:
            return list(qs.values(*fields))
        return list(qs.values())
    except LookupError:
        logger.warning("Model %s.%s not found, skipping", app_label, model_name)
        return []
    except Exception as e:
        logger.warning("Failed to export %s.%s: %s", app_label, model_name, e)
        return []


def _serialize_for_json(obj):
    """JSON serializer for objects not serializable by default."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, (bytes, bytearray)):
        import base64
        return base64.b64encode(obj).decode("utf-8")
    if isinstance(obj, memoryview):
        import base64
        return base64.b64encode(bytes(obj)).decode("utf-8")
    if hasattr(obj, "__str__"):
        return str(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


# ======================================================================
# Settings backup
# ======================================================================

class SettingsBackupService:
    """
    Collects all hub configuration from the database and serializes
    it to a compressed JSON file.
    """

    # Map category names to their export methods
    CATEGORY_EXPORTERS = {
        "cameras": "_export_cameras",
        "camera_settings": "_export_camera_settings",
        "camera_zones": "_export_camera_zones",
        "faces": "_export_faces",
        "vehicles": "_export_vehicles",
        "alarm_settings": "_export_alarm_settings",
        "garage_door_settings": "_export_garage_door_settings",
        "external_devices": "_export_external_devices",
        "meross_accounts": "_export_meross_accounts",
        "meross_devices": "_export_meross_devices",
        "backup_schedules": "_export_backup_schedules",
    }

    def collect_settings(self, categories=None):
        """
        Gather configuration tables into a single dict structure.
        Each key represents a logical group of settings.

        Args:
            categories: list of category names to include, or None for all.
        """
        logger.info("Collecting hub settings for backup (categories=%s)...", categories or "all")

        data = {
            "meta": {
                "version": BACKUP_VERSION,
                "hub_id": _get_hub_id(),
                "created_at": timezone.now().isoformat(),
                "backup_type": "settings",
            },
        }

        active = categories or list(self.CATEGORY_EXPORTERS.keys())
        for cat in active:
            method_name = self.CATEGORY_EXPORTERS.get(cat)
            if method_name:
                data[cat] = getattr(self, method_name)()

        total_items = sum(
            len(v) for k, v in data.items() if isinstance(v, list)
        )
        logger.info("Settings collection complete: %d total items", total_items)
        return data

    def _export_cameras(self):
        return _safe_model_export("camera", "Camera")

    def _export_camera_settings(self):
        return _safe_model_export("camera", "CameraSetting")

    def _export_camera_zones(self):
        return _safe_model_export("camera", "CameraSettingZone")

    def _export_faces(self):
        """Export facial recognition data with embeddings."""
        return _safe_model_export("facial", "Facial")

    def _export_vehicles(self):
        return _safe_model_export("vehicle", "Vehicle")

    def _export_alarm_settings(self):
        return _safe_model_export("automation", "AlarmSettings")

    def _export_garage_door_settings(self):
        return _safe_model_export("automation", "GarageDoorSettings")

    def _export_external_devices(self):
        return _safe_model_export("external_device", "ExternalDevice")

    def _export_meross_accounts(self):
        return _safe_query(
            "SELECT * FROM meross_merossaccount"
        )

    def _export_meross_devices(self):
        return _safe_query(
            "SELECT * FROM meross_merossdevice"
        )

    def _export_backup_schedules(self):
        from .models import BackupSchedule
        try:
            return list(BackupSchedule.objects.values())
        except Exception as e:
            logger.warning("Failed to export backup schedules: %s", e)
            return []

    def create_backup_file(self, output_dir=None, categories=None):
        """
        Collect settings and write to a compressed JSON file.
        Returns the path to the created file.
        """
        data = self.collect_settings(categories=categories)

        if output_dir is None:
            output_dir = tempfile.mkdtemp(prefix="secureprotect_backup_")

        timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
        hub_id = _get_hub_id()
        filename = f"settings_{hub_id}_{timestamp}.json.gz"
        filepath = os.path.join(output_dir, filename)

        json_bytes = json.dumps(data, default=_serialize_for_json, indent=2).encode("utf-8")

        with gzip.open(filepath, "wb") as f:
            f.write(json_bytes)

        file_size = os.path.getsize(filepath)
        logger.info(
            "Settings backup created: %s (%s, %s uncompressed)",
            filepath,
            _format_bytes(file_size),
            _format_bytes(len(json_bytes)),
        )
        return filepath, filename


# ======================================================================
# Media backup
# ======================================================================

class MediaBackupService:
    """
    Archives Frigate recordings, clips, and snapshots into a tar.gz file.
    """

    # Map type names to their filesystem paths
    TYPE_PATH_MAP = {
        "recordings": FRIGATE_RECORDINGS_PATH,
        "clips": FRIGATE_CLIPS_PATH,
        "snapshots": FRIGATE_SNAPSHOTS_PATH,
    }

    def _dir_size(self, path):
        """Estimate size of a single directory in bytes."""
        if not os.path.isdir(path):
            return 0
        try:
            result = subprocess.run(
                ["du", "-sb", path],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                return int(result.stdout.split()[0])
        except Exception as e:
            logger.warning("Failed to estimate size of %s: %s", path, e)
        # Fallback: walk the directory
        total = 0
        for dirpath, _, filenames in os.walk(path):
            for fn in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, fn))
                except OSError:
                    pass
        return total

    def estimate_size(self):
        """Estimate total media size in bytes without creating an archive."""
        return sum(self._dir_size(p) for p in self.TYPE_PATH_MAP.values())

    def estimate_sizes_by_type(self):
        """Return per-type sizes in bytes: {recordings: N, clips: N, snapshots: N}."""
        return {name: self._dir_size(path) for name, path in self.TYPE_PATH_MAP.items()}

    def create_backup_file(self, output_dir=None, media_types=None, incremental_since=None):
        """
        Create a tar.gz archive of selected Frigate media.

        Args:
            output_dir: Where to write the archive.
            media_types: list of 'recordings', 'clips', 'snapshots' or None for all.
            incremental_since: datetime — only include files modified after this time.
                               None = full backup (all files).
        Returns (filepath, filename, file_count).
        """
        if output_dir is None:
            output_dir = tempfile.mkdtemp(prefix="secureprotect_media_backup_")

        if media_types is None:
            media_types = list(self.TYPE_PATH_MAP.keys())

        timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
        hub_id = _get_hub_id()
        mode = "incremental" if incremental_since else "full"
        filename = f"media_{hub_id}_{timestamp}_{mode}.tar.gz"
        filepath = os.path.join(output_dir, filename)

        since_ts = None
        if incremental_since:
            since_ts = incremental_since.timestamp()
            logger.info(
                "Creating INCREMENTAL media archive (since %s) at %s (types: %s) ...",
                incremental_since.isoformat(), filepath, media_types,
            )
        else:
            logger.info("Creating FULL media archive at %s (types: %s) ...", filepath, media_types)

        dirs_to_archive = []
        for mt in media_types:
            path = self.TYPE_PATH_MAP.get(mt)
            if path and os.path.isdir(path):
                dirs_to_archive.append((mt, path))
            else:
                logger.warning("Media directory does not exist or unknown type, skipping: %s", mt)

        if not dirs_to_archive:
            logger.warning("No media directories found. Creating empty archive.")

        file_count = 0
        with tarfile.open(filepath, "w:gz") as tar:
            for mt, dir_path in dirs_to_archive:
                arcname_base = os.path.basename(dir_path)

                if since_ts is None:
                    # Full backup — add entire directory
                    logger.info("Adding %s to archive as %s/", dir_path, arcname_base)
                    tar.add(dir_path, arcname=arcname_base)
                    for _, _, files in os.walk(dir_path):
                        file_count += len(files)
                else:
                    # Incremental — walk and add only new files
                    added = 0
                    for dirpath, dirnames, filenames in os.walk(dir_path):
                        for fn in filenames:
                            full_path = os.path.join(dirpath, fn)
                            try:
                                if os.path.getmtime(full_path) > since_ts:
                                    rel = os.path.relpath(full_path, dir_path)
                                    tar.add(full_path, arcname=os.path.join(arcname_base, rel))
                                    added += 1
                            except OSError:
                                pass
                    file_count += added
                    logger.info("Incremental: added %d new files from %s", added, arcname_base)

        file_size = os.path.getsize(filepath)
        logger.info(
            "Media backup created: %s (%s, %d files, %s)",
            filepath, _format_bytes(file_size), file_count, mode,
        )
        return filepath, filename, file_count


# ======================================================================
# Settings restore
# ======================================================================

class SettingsRestoreService:
    """
    Reads a settings backup JSON and recreates database records.
    Designed to be idempotent -- existing records are updated or
    skipped rather than duplicated.
    """

    # Tables that should NOT be restored (user must re-create these)
    SKIP_TABLES = {"auth_user", "ring_account"}

    def restore_from_file(self, filepath):
        """
        Restore settings from a compressed JSON backup file.
        Returns a summary dict.
        """
        logger.info("Starting settings restore from %s", filepath)

        if filepath.endswith(".gz"):
            with gzip.open(filepath, "rb") as f:
                raw = f.read()
        else:
            with open(filepath, "rb") as f:
                raw = f.read()

        data = json.loads(raw.decode("utf-8"))

        meta = data.get("meta", {})
        version = meta.get("version", "unknown")
        source_hub = meta.get("hub_id", "unknown")
        logger.info(
            "Restoring backup version=%s from hub=%s created=%s",
            version,
            source_hub,
            meta.get("created_at", "unknown"),
        )

        summary = {}

        # Restore in dependency order
        restore_steps = [
            ("cameras", self._restore_cameras),
            ("camera_settings", self._restore_camera_settings),
            ("camera_zones", self._restore_camera_zones),
            ("faces", self._restore_faces),
            ("vehicles", self._restore_vehicles),
            ("alarm_settings", self._restore_alarm_settings),
            ("garage_door_settings", self._restore_garage_door_settings),
            ("external_devices", self._restore_external_devices),
            ("meross_accounts", self._restore_meross_accounts),
            ("meross_devices", self._restore_meross_devices),
            ("backup_schedules", self._restore_backup_schedules),
        ]

        for key, restore_fn in restore_steps:
            items = data.get(key, [])
            if not items:
                summary[key] = {"status": "skipped", "count": 0}
                continue

            try:
                count = restore_fn(items)
                summary[key] = {"status": "restored", "count": count}
                logger.info("Restored %d items for '%s'", count, key)
            except Exception as e:
                summary[key] = {"status": "error", "error": str(e)}
                logger.error("Failed to restore '%s': %s", key, e)

        logger.info("Restore complete. Summary: %s", summary)
        return summary

    def _restore_generic_model(self, app_label, model_name, items, lookup_field="id"):
        """
        Generic restore: update existing records or create new ones.
        Returns the number of records processed.
        """
        try:
            Model = apps.get_model(app_label, model_name)
        except LookupError:
            logger.warning("Model %s.%s not found, skipping restore", app_label, model_name)
            return 0

        count = 0
        for item in items:
            item_copy = dict(item)
            # Remove auto-generated fields that should not be set directly
            for auto_field in ["id", "created_at", "updated_at"]:
                item_copy.pop(auto_field, None)

            lookup_value = item.get(lookup_field)
            if lookup_value and lookup_field != "id":
                try:
                    obj, created = Model.objects.update_or_create(
                        **{lookup_field: lookup_value},
                        defaults=item_copy,
                    )
                    count += 1
                except Exception as e:
                    logger.warning("Failed to restore %s record: %s", model_name, e)
            else:
                try:
                    Model.objects.create(**item_copy)
                    count += 1
                except Exception as e:
                    logger.warning("Failed to create %s record: %s", model_name, e)

        return count

    def _restore_cameras(self, items):
        return self._restore_generic_model("camera", "Camera", items, lookup_field="slug_name")

    def _restore_camera_settings(self, items):
        return self._restore_generic_model("camera", "CameraSetting", items)

    def _restore_camera_zones(self, items):
        return self._restore_generic_model("camera", "CameraSettingZone", items)

    def _restore_faces(self, items):
        return self._restore_generic_model("facial", "Facial", items, lookup_field="name")

    def _restore_vehicles(self, items):
        return self._restore_generic_model("vehicle", "Vehicle", items, lookup_field="license_plate")

    def _restore_alarm_settings(self, items):
        return self._restore_generic_model("automation", "AlarmSettings", items)

    def _restore_garage_door_settings(self, items):
        return self._restore_generic_model("automation", "GarageDoorSettings", items)

    def _restore_external_devices(self, items):
        return self._restore_generic_model("external_device", "ExternalDevice", items)

    def _restore_meross_accounts(self, items):
        """Restore Meross accounts via raw SQL since there may not be a Django model."""
        count = 0
        for item in items:
            try:
                with connection.cursor() as cursor:
                    item_copy = dict(item)
                    item_copy.pop("id", None)
                    cols = ", ".join(item_copy.keys())
                    placeholders = ", ".join(["%s"] * len(item_copy))
                    sql = f"INSERT INTO meross_merossaccount ({cols}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
                    cursor.execute(sql, list(item_copy.values()))
                    count += 1
            except Exception as e:
                logger.warning("Failed to restore Meross account: %s", e)
        return count

    def _restore_meross_devices(self, items):
        """Restore Meross devices via raw SQL."""
        count = 0
        for item in items:
            try:
                with connection.cursor() as cursor:
                    item_copy = dict(item)
                    item_copy.pop("id", None)
                    cols = ", ".join(item_copy.keys())
                    placeholders = ", ".join(["%s"] * len(item_copy))
                    sql = f"INSERT INTO meross_merossdevice ({cols}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
                    cursor.execute(sql, list(item_copy.values()))
                    count += 1
            except Exception as e:
                logger.warning("Failed to restore Meross device: %s", e)
        return count

    def _restore_backup_schedules(self, items):
        from .models import BackupSchedule

        count = 0
        for item in items:
            try:
                item_copy = dict(item)
                for key in ["id", "created_at", "updated_at", "last_run", "next_run"]:
                    item_copy.pop(key, None)
                schedule = BackupSchedule.objects.create(**item_copy)
                schedule.compute_next_run()
                schedule.save(update_fields=["next_run"])
                count += 1
            except Exception as e:
                logger.warning("Failed to restore backup schedule: %s", e)
        return count


# ======================================================================
# Media restore
# ======================================================================

class MediaRestoreService:
    """
    Extracts a media backup tar.gz to the Frigate storage directory.
    """

    def restore_from_file(self, filepath):
        """
        Extract media archive to the Frigate storage base path.
        Returns a summary dict.
        """
        logger.info("Starting media restore from %s", filepath)

        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Backup file not found: {filepath}")

        # Ensure destination exists
        os.makedirs(FRIGATE_STORAGE_BASE, exist_ok=True)

        with tarfile.open(filepath, "r:gz") as tar:
            # Security: check for path traversal
            for member in tar.getmembers():
                member_path = os.path.join(FRIGATE_STORAGE_BASE, member.name)
                abs_base = os.path.abspath(FRIGATE_STORAGE_BASE)
                abs_member = os.path.abspath(member_path)
                if not abs_member.startswith(abs_base):
                    raise ValueError(
                        f"Potentially unsafe path in archive: {member.name}"
                    )

            tar.extractall(path=FRIGATE_STORAGE_BASE)

        logger.info("Media restore complete to %s", FRIGATE_STORAGE_BASE)

        # Count restored files
        total_files = 0
        total_size = 0
        for dirpath, _, filenames in os.walk(FRIGATE_STORAGE_BASE):
            for fn in filenames:
                total_files += 1
                try:
                    total_size += os.path.getsize(os.path.join(dirpath, fn))
                except OSError:
                    pass

        return {
            "status": "restored",
            "files": total_files,
            "total_size": total_size,
            "total_size_display": _format_bytes(total_size),
        }


# ======================================================================
# Backup size estimation
# ======================================================================

def estimate_backup_sizes():
    """
    Estimate the size of settings and media backups without creating them.
    Returns a dict with estimates in MB.
    """
    # Settings estimate: run a quick collection and measure JSON size
    settings_size_mb = 0.5  # Default small estimate
    try:
        svc = SettingsBackupService()
        data = svc.collect_settings()
        json_bytes = json.dumps(data, default=_serialize_for_json).encode("utf-8")
        # Gzip typically compresses JSON to ~15-20% of original
        settings_size_mb = round(len(json_bytes) * 0.2 / (1024 * 1024), 2)
    except Exception as e:
        logger.warning("Failed to estimate settings backup size: %s", e)

    # Media estimate (total + per-type)
    media_size_mb = 0.0
    per_type = {"estimated_recordings_mb": 0.0, "estimated_clips_mb": 0.0, "estimated_snapshots_mb": 0.0}
    try:
        media_svc = MediaBackupService()
        by_type = media_svc.estimate_sizes_by_type()
        for name, raw in by_type.items():
            compressed = round(raw * 0.95 / (1024 * 1024), 2)
            per_type[f"estimated_{name}_mb"] = compressed
            media_size_mb += compressed
        media_size_mb = round(media_size_mb, 2)
    except Exception as e:
        logger.warning("Failed to estimate media backup size: %s", e)

    return {
        "estimated_settings_backup_mb": settings_size_mb,
        "estimated_media_backup_mb": media_size_mb,
        **per_type,
    }
