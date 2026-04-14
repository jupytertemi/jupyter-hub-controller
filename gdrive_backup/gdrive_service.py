"""
Google Drive API service wrapper.

Handles OAuth token lifecycle, folder management, resumable uploads/downloads,
storage quota queries, and file deletion. All operations use service-level
retry logic so callers can treat this as a reliable transport layer.
"""

import io
import logging
import os
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

# OAuth credentials -- read from environment with provided defaults.
GOOGLE_CLIENT_ID = os.environ.get(
    "GOOGLE_CLIENT_ID",
    "",
)
GOOGLE_CLIENT_SECRET = os.environ.get(
    "GOOGLE_CLIENT_SECRET",
    "",
)

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]

# 5 MB chunk size for resumable uploads (Google minimum is 256 KB).
UPLOAD_CHUNK_SIZE = 5 * 1024 * 1024

# Root folder name in the user's Google Drive.
ROOT_FOLDER_NAME = "SecureProtect Backups"


def _get_hub_id():
    """Return the hub identifier used as a subfolder name in Google Drive."""
    try:
        from utils.update_env import read_env_file
        hub_id = read_env_file("DEVICE_NAME")
        if hub_id and hub_id.strip():
            return hub_id.strip()
    except Exception:
        pass
    return "default-hub"


def _format_bytes(size_bytes):
    """Convert bytes to a human-readable string."""
    if size_bytes == 0:
        return "0 B"
    size = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(size) < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"


class GoogleDriveServiceError(Exception):
    """Raised when a Google Drive API operation fails."""
    pass


class GoogleDriveService:
    """
    Encapsulates all Google Drive interactions for SecureProtect backups.

    Usage::

        svc = GoogleDriveService()
        svc.load_credentials()       # loads from DB, refreshes if expired
        folder_id = svc.get_or_create_backup_folder()
        file_id = svc.upload_file("/path/to/backup.tar.gz", "backup.tar.gz", folder_id)
        svc.download_file(file_id, "/path/to/restore.tar.gz")
        svc.delete_file(file_id)
    """

    def __init__(self):
        self._credentials = None
        self._drive_service = None
        self._oauth2_service = None

    # ------------------------------------------------------------------
    # OAuth helpers
    # ------------------------------------------------------------------

    @staticmethod
    def get_auth_url(redirect_uri):
        """
        Build the Google OAuth2 authorization URL that the Flutter app
        will open in a webview.
        """
        from google_auth_oauthlib.flow import Flow

        flow = Flow.from_client_config(
            client_config={
                "web": {
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [redirect_uri],
                }
            },
            scopes=SCOPES,
        )
        flow.redirect_uri = redirect_uri
        auth_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )
        return auth_url, state

    @staticmethod
    def exchange_code(code, redirect_uri=""):
        """
        Exchange an authorization code for access + refresh tokens.

        For Flutter mobile (google_sign_in SDK): redirect_uri is "" (default).
        For web browser redirect: redirect_uri is the callback URL.
        """
        import json
        import urllib.parse
        import urllib.request

        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        # Direct POST to Google's token endpoint — works for both
        # mobile SDK codes (redirect_uri="") and web redirect codes.
        token_body = urllib.parse.urlencode({
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }).encode()

        req = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=token_body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                token_response = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise GoogleDriveServiceError(
                f"Token exchange failed ({exc.code}): {body}"
            ) from exc

        access_token = token_response["access_token"]
        refresh_token = token_response.get("refresh_token")
        expires_in = token_response.get("expires_in", 3600)
        granted_scope = token_response.get("scope", "")

        # Build credentials for the userinfo API call.
        # Don't pass scopes — the token already has the granted scopes
        # baked in. Passing extra scopes here causes refresh() to send
        # them to Google, which rejects scopes not in the original grant.
        credentials = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET,
        )

        # Fetch the user's email address
        oauth2_service = build("oauth2", "v2", credentials=credentials)
        user_info = oauth2_service.userinfo().get().execute()
        email = user_info.get("email", "")

        token_expiry = timezone.now() + timedelta(seconds=expires_in)

        has_drive = "drive.file" in granted_scope or "drive" in granted_scope

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_expiry": token_expiry,
            "email": email,
            "scope": granted_scope,
            "has_drive_scope": has_drive,
        }

    @staticmethod
    def start_device_code():
        """
        Start Device Code OAuth flow.

        POST to Google's device/code endpoint. Returns user_code and
        verification_uri for the user to enter on their phone/browser.
        No redirect URI needed — works on plain HTTP hub IPs.
        """
        import json
        import urllib.parse
        import urllib.request

        if not GOOGLE_CLIENT_ID:
            raise GoogleDriveServiceError(
                "Google Drive not configured. GOOGLE_CLIENT_ID required."
            )

        # Device Code flow: request ONLY drive.file scope.
        # Adding userinfo.email or openid causes Google to silently
        # drop drive.file for "TVs and Limited Input" client types.
        # We fetch the user's email from the Drive API after auth.
        device_scopes = [
            "https://www.googleapis.com/auth/drive.file",
        ]

        body = urllib.parse.urlencode({
            "client_id": GOOGLE_CLIENT_ID,
            "scope": " ".join(device_scopes),
        }).encode()

        req = urllib.request.Request(
            "https://oauth2.googleapis.com/device/code",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            err = exc.read().decode("utf-8", errors="replace")
            raise GoogleDriveServiceError(
                f"Device code request failed ({exc.code}): {err}"
            ) from exc

        if "user_code" not in data:
            raise GoogleDriveServiceError(
                data.get("error_description", "Failed to start device code flow")
            )

        return {
            "device_code": data["device_code"],
            "user_code": data["user_code"],
            "verification_uri": data.get(
                "verification_url", "https://www.google.com/device"
            ),
            "interval": data.get("interval", 5),
            "expires_in": data.get("expires_in", 1800),
        }

    @staticmethod
    def poll_device_code(device_code):
        """
        Poll Google's token endpoint to check if the user has completed
        the device code sign-in.

        Returns:
          - {"status": "pending"} if user hasn't authorized yet
          - {"status": "error", "error": "..."} on failure
          - Full token dict (access_token, refresh_token, token_expiry, email) on success
        """
        import json
        import urllib.parse
        import urllib.request

        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        body = urllib.parse.urlencode({
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "device_code": device_code,
        }).encode()

        req = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            err_body = json.loads(exc.read().decode("utf-8", errors="replace"))
            error = err_body.get("error", "")
            if error == "authorization_pending":
                return {"status": "pending"}
            if error == "slow_down":
                return {"status": "pending", "message": "slow_down"}
            return {
                "status": "error",
                "error": err_body.get("error_description", error),
            }

        # Success — we have tokens
        access_token = data["access_token"]
        refresh_token = data.get("refresh_token")
        expires_in = data.get("expires_in", 3600)
        granted_scope = data.get("scope", "")

        # Fetch user email from Drive API about endpoint.
        # The token only has drive.file scope (no userinfo), so we
        # use the Drive API which works with drive.file scope.
        email = ""
        try:
            credentials = Credentials(
                token=access_token,
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=GOOGLE_CLIENT_ID,
                client_secret=GOOGLE_CLIENT_SECRET,
            )
            drive_service = build("drive", "v3", credentials=credentials)
            about = drive_service.about().get(fields="user").execute()
            email = about.get("user", {}).get("emailAddress", "")
        except Exception as e:
            logger.warning("Could not fetch email after device code auth: %s", e)

        token_expiry = timezone.now() + timedelta(seconds=expires_in)

        has_drive = "drive.file" in granted_scope or "drive" in granted_scope

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_expiry": token_expiry,
            "email": email,
            "scope": granted_scope,
            "has_drive_scope": has_drive,
        }

    @staticmethod
    def revoke_token(token):
        """Revoke an OAuth2 token with Google's servers."""
        import requests

        try:
            resp = requests.post(
                "https://oauth2.googleapis.com/revoke",
                params={"token": token},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=10,
            )
            if resp.status_code == 200:
                logger.info("Google OAuth token revoked successfully")
                return True
            else:
                logger.warning(
                    "Token revocation returned status %d: %s",
                    resp.status_code,
                    resp.text,
                )
                return False
        except Exception as e:
            logger.error("Failed to revoke token: %s", e)
            return False

    # ------------------------------------------------------------------
    # Credential management
    # ------------------------------------------------------------------

    def load_credentials(self):
        """
        Load OAuth credentials from the database, refreshing the access
        token automatically if it has expired.
        """
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request

        from .models import GoogleDriveAccount

        try:
            account = GoogleDriveAccount.objects.filter(is_active=True).first()
        except Exception as e:
            raise GoogleDriveServiceError(f"Database error loading account: {e}")

        if not account:
            raise GoogleDriveServiceError(
                "No active Google Drive account. Please connect an account first."
            )

        access_token = account.access_token
        refresh_token = account.refresh_token

        if not refresh_token:
            raise GoogleDriveServiceError(
                "No refresh token stored. Please reconnect your Google account."
            )

        # Don't pass scopes — the token defines its own scope.
        # Device Code flow grants drive.file only; popup flow grants
        # drive.file + drive.metadata.readonly. Passing SCOPES here
        # causes refresh() to request scopes not in the original grant,
        # which makes the refreshed token lose drive.file → 403 on
        # files.create(). Without scopes, refresh preserves the
        # original grant.
        self._credentials = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET,
        )

        # Refresh if expired or about to expire (within 5 minutes)
        if account.is_token_expired or (
            account.token_expiry
            and account.token_expiry - timezone.now() < timedelta(minutes=5)
        ):
            try:
                self._credentials.refresh(Request())
                # Persist the refreshed token
                account.access_token = self._credentials.token
                if self._credentials.expiry:
                    expiry = self._credentials.expiry
                    if expiry.tzinfo is None:
                        expiry = timezone.make_aware(expiry, timezone.utc)
                    account.token_expiry = expiry
                account.save(update_fields=["_access_token", "token_expiry", "updated_at"])
                logger.info("Google OAuth access token refreshed for %s", account.email)
            except Exception as e:
                logger.error("Failed to refresh Google OAuth token: %s", e)
                raise GoogleDriveServiceError(
                    f"Token refresh failed. Please reconnect your Google account. Error: {e}"
                )

    def _get_drive_service(self):
        """Lazy-initialize the Drive API client."""
        if self._drive_service is None:
            from googleapiclient.discovery import build
            if self._credentials is None:
                self.load_credentials()
            self._drive_service = build("drive", "v3", credentials=self._credentials)
        return self._drive_service

    # ------------------------------------------------------------------
    # Folder management
    # ------------------------------------------------------------------

    def _find_folder(self, name, parent_id=None):
        """
        Find a folder by name, optionally within a parent folder.
        Returns the folder ID or None.
        With drive.file scope, files.list may 403 -- returns None so
        the caller falls through to _create_folder.
        """
        service = self._get_drive_service()
        query = (
            f"name = '{name}' "
            f"and mimeType = 'application/vnd.google-apps.folder' "
            f"and trashed = false"
        )
        if parent_id:
            query += f" and '{parent_id}' in parents"

        try:
            results = (
                service.files()
                .list(q=query, spaces="drive", fields="files(id, name)", pageSize=1)
                .execute()
            )
            files = results.get("files", [])
            return files[0]["id"] if files else None
        except Exception as e:
            err_str = str(e)
            if "insufficientPermissions" in err_str or "403" in err_str:
                logger.info("Cannot search folders (drive.file scope) - will create")
                return None
            logger.error("Error searching for folder '%s': %s", name, e)
            raise GoogleDriveServiceError(f"Failed to search for folder: {e}")

    def _folder_exists(self, folder_id):
        """Check if a folder ID still exists on Drive. Works with drive.file scope."""
        service = self._get_drive_service()
        try:
            meta = service.files().get(fileId=folder_id, fields="id,trashed").execute()
            return not meta.get("trashed", False)
        except Exception:
            return False

    def _create_folder(self, name, parent_id=None):
        """Create a folder in Google Drive. Returns the new folder ID."""
        service = self._get_drive_service()
        metadata = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if parent_id:
            metadata["parents"] = [parent_id]

        try:
            folder = service.files().create(body=metadata, fields="id").execute()
            logger.info("Created Google Drive folder '%s' (id=%s)", name, folder["id"])
            return folder["id"]
        except Exception as e:
            logger.error("Failed to create folder '%s': %s", name, e)
            raise GoogleDriveServiceError(f"Failed to create folder: {e}")

    def get_or_create_backup_folder(self):
        """
        Ensure the folder hierarchy exists:
          My Drive / SecureProtect Backups / {hub_id}

        Returns the hub subfolder ID.

        With drive.file scope, files.list is blocked (403). So we first
        try to reuse the folder from the last successful backup, then
        fall back to creating new folders.
        """
        from .models import BackupRecord

        # Try reusing folder from last successful backup
        last = (
            BackupRecord.objects.filter(
                status=BackupRecord.Status.COMPLETED,
                gdrive_folder_id__gt="",
            )
            .order_by("-completed_at")
            .first()
        )
        if last and self._folder_exists(last.gdrive_folder_id):
            return last.gdrive_folder_id

        # Search or create root folder
        root_id = self._find_folder(ROOT_FOLDER_NAME)
        if not root_id:
            root_id = self._create_folder(ROOT_FOLDER_NAME)

        # Search or create hub subfolder
        hub_id = _get_hub_id()
        hub_folder_id = self._find_folder(hub_id, parent_id=root_id)
        if not hub_folder_id:
            hub_folder_id = self._create_folder(hub_id, parent_id=root_id)

        return hub_folder_id

    # ------------------------------------------------------------------
    # File upload (resumable)
    # ------------------------------------------------------------------

    def upload_file(self, file_path, filename, folder_id):
        """
        Upload a file to Google Drive using resumable upload.
        Suitable for files of any size (tested up to 30 GB+).

        Returns a dict with file ID and size.
        """
        from googleapiclient.http import MediaFileUpload

        service = self._get_drive_service()

        file_metadata = {
            "name": filename,
            "parents": [folder_id],
        }

        file_size = os.path.getsize(file_path)

        # Determine MIME type
        if filename.endswith(".tar.gz") or filename.endswith(".tgz"):
            mime_type = "application/gzip"
        elif filename.endswith(".json.gz"):
            mime_type = "application/gzip"
        elif filename.endswith(".json"):
            mime_type = "application/json"
        else:
            mime_type = "application/octet-stream"

        media = MediaFileUpload(
            file_path,
            mimetype=mime_type,
            chunksize=UPLOAD_CHUNK_SIZE,
            resumable=True,
        )

        try:
            request = service.files().create(
                body=file_metadata,
                media_body=media,
                fields="id, name, size",
            )

            response = None
            retry_count = 0
            max_retries = 10

            while response is None:
                try:
                    status, response = request.next_chunk()
                    if status:
                        progress = int(status.progress() * 100)
                        logger.info(
                            "Upload progress for '%s': %d%% (%s / %s)",
                            filename,
                            progress,
                            _format_bytes(int(file_size * status.progress())),
                            _format_bytes(file_size),
                        )
                    retry_count = 0  # Reset on success
                except Exception as chunk_error:
                    retry_count += 1
                    if retry_count > max_retries:
                        raise GoogleDriveServiceError(
                            f"Upload failed after {max_retries} retries: {chunk_error}"
                        )
                    import time
                    wait_time = min(2 ** retry_count, 60)
                    logger.warning(
                        "Upload chunk failed (retry %d/%d), waiting %ds: %s",
                        retry_count,
                        max_retries,
                        wait_time,
                        chunk_error,
                    )
                    time.sleep(wait_time)

            file_id = response.get("id")
            uploaded_size = int(response.get("size", file_size))
            logger.info(
                "Upload complete: '%s' -> file_id=%s (%s)",
                filename,
                file_id,
                _format_bytes(uploaded_size),
            )
            return {"file_id": file_id, "size": uploaded_size}

        except GoogleDriveServiceError:
            raise
        except Exception as e:
            logger.error("Upload failed for '%s': %s", filename, e)
            raise GoogleDriveServiceError(f"Upload failed: {e}")

    # ------------------------------------------------------------------
    # File download
    # ------------------------------------------------------------------

    def download_file(self, file_id, destination_path):
        """
        Download a file from Google Drive to a local path.
        Uses chunked download for large files.
        """
        from googleapiclient.http import MediaIoBaseDownload

        service = self._get_drive_service()

        try:
            request = service.files().get_media(fileId=file_id)

            os.makedirs(os.path.dirname(destination_path), exist_ok=True)

            with open(destination_path, "wb") as fh:
                downloader = MediaIoBaseDownload(fh, request, chunksize=UPLOAD_CHUNK_SIZE)
                done = False
                retry_count = 0
                max_retries = 10

                while not done:
                    try:
                        status, done = downloader.next_chunk()
                        if status:
                            logger.info(
                                "Download progress: %d%%",
                                int(status.progress() * 100),
                            )
                        retry_count = 0
                    except Exception as chunk_error:
                        retry_count += 1
                        if retry_count > max_retries:
                            raise GoogleDriveServiceError(
                                f"Download failed after {max_retries} retries: {chunk_error}"
                            )
                        import time
                        wait_time = min(2 ** retry_count, 60)
                        logger.warning(
                            "Download chunk failed (retry %d/%d), waiting %ds: %s",
                            retry_count,
                            max_retries,
                            wait_time,
                            chunk_error,
                        )
                        time.sleep(wait_time)

            logger.info(
                "Download complete: file_id=%s -> %s (%s)",
                file_id,
                destination_path,
                _format_bytes(os.path.getsize(destination_path)),
            )
            return destination_path

        except GoogleDriveServiceError:
            raise
        except Exception as e:
            # Clean up partial downloads
            if os.path.exists(destination_path):
                try:
                    os.remove(destination_path)
                except OSError:
                    pass
            logger.error("Download failed for file_id=%s: %s", file_id, e)
            raise GoogleDriveServiceError(f"Download failed: {e}")

    # ------------------------------------------------------------------
    # File listing and metadata
    # ------------------------------------------------------------------

    def list_files(self, folder_id):
        """List all files in a Google Drive folder. Returns list of dicts."""
        service = self._get_drive_service()

        try:
            results = (
                service.files()
                .list(
                    q=f"'{folder_id}' in parents and trashed = false",
                    spaces="drive",
                    fields="files(id, name, size, createdTime, modifiedTime, mimeType)",
                    orderBy="createdTime desc",
                    pageSize=100,
                )
                .execute()
            )
            return results.get("files", [])
        except Exception as e:
            logger.error("Failed to list files in folder %s: %s", folder_id, e)
            raise GoogleDriveServiceError(f"Failed to list files: {e}")

    def get_file_metadata(self, file_id):
        """Get metadata for a specific file."""
        service = self._get_drive_service()

        try:
            return (
                service.files()
                .get(fileId=file_id, fields="id, name, size, createdTime, mimeType")
                .execute()
            )
        except Exception as e:
            logger.error("Failed to get metadata for file %s: %s", file_id, e)
            raise GoogleDriveServiceError(f"Failed to get file metadata: {e}")

    # ------------------------------------------------------------------
    # File deletion
    # ------------------------------------------------------------------

    def delete_file(self, file_id):
        """Permanently delete a file from Google Drive."""
        service = self._get_drive_service()

        try:
            service.files().delete(fileId=file_id).execute()
            logger.info("Deleted Google Drive file: %s", file_id)
            return True
        except Exception as e:
            logger.error("Failed to delete file %s: %s", file_id, e)
            raise GoogleDriveServiceError(f"Failed to delete file: {e}")

    # ------------------------------------------------------------------
    # Storage quota
    # ------------------------------------------------------------------

    def get_storage_quota(self):
        """
        Get the user's Google Drive storage quota.
        Returns a dict with total, used, and available bytes.
        """
        service = self._get_drive_service()

        try:
            about = service.about().get(fields="storageQuota").execute()
            quota = about.get("storageQuota", {})

            # Google Drive returns strings for these values
            total = int(quota.get("limit", 0))
            used = int(quota.get("usage", 0))
            available = max(total - used, 0) if total > 0 else 0

            return {
                "total_bytes": total,
                "used_bytes": used,
                "available_bytes": available,
                "total_display": _format_bytes(total),
                "used_display": _format_bytes(used),
                "available_display": _format_bytes(available),
                "usage_percent": round((used / total * 100), 1) if total > 0 else 0.0,
            }
        except Exception as e:
            logger.error("Failed to get storage quota: %s", e)
            raise GoogleDriveServiceError(f"Failed to get storage quota: {e}")
