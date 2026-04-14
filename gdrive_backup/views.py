"""
API views for Google Drive backup management.

Provides endpoints for OAuth flow, backup/restore operations,
storage management, and scheduling. All views use DRF's
APIView or ModelViewSet for consistent error handling and
serialization.
"""

import logging

from django.http import HttpResponse as DjangoHttpResponse
from django.utils import timezone
from rest_framework import generics, mixins, status, viewsets
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .backup_service import estimate_backup_sizes
from .gdrive_service import GoogleDriveService, GoogleDriveServiceError
from .models import BackupRecord, BackupSchedule, GoogleDriveAccount
from .serializers import (
    BackupRecordDetailSerializer,
    BackupRecordSerializer,
    BackupScheduleSerializer,
    DriveSpaceSerializer,
    GoogleDriveAccountSerializer,
    RestoreBackupSerializer,
    StartBackupSerializer,
)


def _get_scheme(request):
    """Detect scheme behind reverse proxy (Cloudflare/HAProxy)."""
    if request.META.get("HTTP_X_FORWARDED_PROTO") == "https":
        return "https"
    return "https" if request.is_secure() else "http"
from .tasks import run_backup, run_restore

logger = logging.getLogger(__name__)


# ======================================================================
# OAuth views
# ======================================================================


class OAuthURLView(APIView):
    """
    GET /api/gdrive/auth/url
    Returns the Google OAuth authorization URL.
    The Flutter app opens this in a webview.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        redirect_uri = request.query_params.get("redirect_uri")
        if not redirect_uri:
            # Build from request host (works behind Cloudflare tunnels)
            scheme = _get_scheme(request)
            host = request.get_host()
            redirect_uri = f"{scheme}://{host}/api/gdrive/auth/callback"

        try:
            auth_url, state = GoogleDriveService.get_auth_url(redirect_uri)
            return Response({
                "auth_url": auth_url,
                "state": state,
                "redirect_uri": redirect_uri,
            })
        except Exception as e:
            logger.error("Failed to generate OAuth URL: %s", e)
            return Response(
                {"error": f"Failed to generate authorization URL: {e}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class OAuthCallbackView(APIView):
    """
    GET  /api/gdrive/auth/callback?code=...&state=...
    POST /api/gdrive/auth/callback  {"code": "...", "redirect_uri": "..."}

    Exchanges the authorization code for tokens and stores them.
    GET is used when Google redirects the browser after OAuth consent.
    POST is used when Flutter sends the code directly.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        """Handle browser redirect from Google OAuth consent screen."""
        code = request.query_params.get("code")
        if not code:
            return DjangoHttpResponse(
                "<html><body style='font-family:sans-serif;text-align:center;padding:60px'>"
                "<h2>Authorization failed</h2>"
                "<p>No authorization code received. Please try again from the app.</p>"
                "</body></html>",
                content_type="text/html",
                status=400,
            )

        scheme = _get_scheme(request)
        host = request.get_host()
        redirect_uri = f"{scheme}://{host}/api/gdrive/auth/callback"

        try:
            token_data = GoogleDriveService.exchange_code(code, redirect_uri)
        except Exception as e:
            logger.error("OAuth token exchange failed (GET): %s", e)
            return DjangoHttpResponse(
                "<html><body style='font-family:sans-serif;text-align:center;padding:60px'>"
                f"<h2>Connection failed</h2>"
                f"<p>{e}</p>"
                "<p>Please close this tab and try again from the app.</p>"
                "</body></html>",
                content_type="text/html",
                status=400,
            )

        # Deactivate any previously active accounts
        GoogleDriveAccount.objects.filter(is_active=True).update(is_active=False)

        account, created = GoogleDriveAccount.objects.update_or_create(
            email=token_data["email"],
            defaults={
                "is_active": True,
                "token_expiry": token_data["token_expiry"],
            },
        )
        account.access_token = token_data["access_token"]
        account.refresh_token = token_data["refresh_token"]
        account.save()

        action = "connected" if created else "reconnected"
        logger.info("Google Drive account %s (GET): %s", action, account.email)

        return DjangoHttpResponse(
            "<html><body style='font-family:sans-serif;text-align:center;padding:60px'>"
            "<h2>Connected to Google Drive</h2>"
            f"<p>Signed in as <strong>{account.email}</strong></p>"
            "<p>You can close this tab and return to the app.</p>"
            "</body></html>",
            content_type="text/html",
        )

    def post(self, request):
        """
        Exchange an auth code for tokens.

        Flutter mobile sends a serverAuthCode from google_sign_in SDK
        with no redirect_uri (defaults to "").  Web browser redirects
        include an explicit redirect_uri.
        """
        code = request.data.get("code")
        redirect_uri = request.data.get("redirect_uri", "")

        if not code:
            return Response(
                {"error": "Authorization code is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            token_data = GoogleDriveService.exchange_code(code, redirect_uri)
        except Exception as e:
            logger.error("OAuth token exchange failed: %s", e)
            return Response(
                {"error": f"Failed to exchange authorization code: {e}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Deactivate any previously active accounts
        GoogleDriveAccount.objects.filter(is_active=True).update(is_active=False)

        # Create or update the account
        account, created = GoogleDriveAccount.objects.update_or_create(
            email=token_data["email"],
            defaults={
                "is_active": True,
                "token_expiry": token_data["token_expiry"],
            },
        )
        account.access_token = token_data["access_token"]
        account.refresh_token = token_data["refresh_token"]
        account.save()

        action = "connected" if created else "reconnected"
        has_drive = token_data.get("has_drive_scope", False)
        logger.info(
            "Google Drive account %s: %s (drive_scope=%s, scope=%s)",
            action, account.email, has_drive, token_data.get("scope", ""),
        )

        return Response({
            "status": action,
            "email": account.email,
            "connected_at": account.connected_at.isoformat(),
            "has_drive_scope": has_drive,
            "scope": token_data.get("scope", ""),
        }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class OAuthStatusView(APIView):
    """
    GET /api/gdrive/auth/status
    Returns the current Google Drive connection status.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        account = GoogleDriveAccount.objects.filter(is_active=True).first()
        if not account:
            return Response({
                "connected": False,
                "email": None,
            })

        return Response({
            "connected": True,
            "email": account.email,
            "connected_at": account.connected_at.isoformat(),
            "token_expired": account.is_token_expired,
        })


class DeviceCodeStartView(APIView):
    """
    POST /api/gdrive/auth/device-start
    Start Device Code OAuth flow. Returns a user_code and verification_uri
    for the user to enter on their phone at google.com/device.
    No HTTPS redirect URI needed — works on plain HTTP hub IPs.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        import time
        from django.core.cache import cache

        try:
            result = GoogleDriveService.start_device_code()
        except GoogleDriveServiceError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Store device_code + expiry in cache for polling
        cache.set(
            "gdrive_device_code",
            {
                "device_code": result["device_code"],
                "expires": time.time() + result["expires_in"],
                "interval": result["interval"],
            },
            timeout=result["expires_in"] + 60,
        )

        return Response({
            "user_code": result["user_code"],
            "verification_uri": result["verification_uri"],
            "expires_in": result["expires_in"],
        })


class DeviceCodePollView(APIView):
    """
    GET /api/gdrive/auth/device-poll
    Poll to check if the user has completed the device code sign-in.
    Returns status: pending | connected | expired | error.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        import time
        from django.core.cache import cache

        state = cache.get("gdrive_device_code")
        if not state:
            return Response(
                {"status": "no_session", "error": "No OAuth session in progress."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if time.time() > state["expires"]:
            cache.delete("gdrive_device_code")
            return Response({"status": "expired", "error": "Code expired. Please try again."})

        try:
            result = GoogleDriveService.poll_device_code(state["device_code"])
        except GoogleDriveServiceError as e:
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        if result.get("status") == "pending":
            return Response({"status": "pending"})

        if result.get("status") == "error":
            cache.delete("gdrive_device_code")
            return Response({"status": "error", "error": result.get("error", "Unknown error")})

        # Success — save tokens (same logic as OAuthCallbackView.post)
        GoogleDriveAccount.objects.filter(is_active=True).update(is_active=False)

        account, created = GoogleDriveAccount.objects.update_or_create(
            email=result["email"],
            defaults={
                "is_active": True,
                "token_expiry": result["token_expiry"],
            },
        )
        account.access_token = result["access_token"]
        account.refresh_token = result["refresh_token"]
        account.save()

        cache.delete("gdrive_device_code")

        action = "connected" if created else "reconnected"
        has_drive = result.get("has_drive_scope", False)
        logger.info(
            "Google Drive account %s (device code): %s (drive_scope=%s)",
            action, account.email, has_drive,
        )

        return Response({
            "status": "connected",
            "email": account.email,
            "has_drive_scope": has_drive,
            "scope": result.get("scope", ""),
        })


class OAuthDisconnectView(APIView):
    """
    POST /api/gdrive/auth/disconnect
    Revokes tokens and disconnects the Google Drive account.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        account = GoogleDriveAccount.objects.filter(is_active=True).first()
        if not account:
            return Response(
                {"error": "No active Google Drive account."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Attempt to revoke the token with Google
        access_token = account.access_token
        if access_token:
            GoogleDriveService.revoke_token(access_token)

        email = account.email
        account.is_active = False
        account.access_token = None
        account.refresh_token = None
        account.token_expiry = None
        account.save()

        logger.info("Google Drive account disconnected: %s", email)

        return Response({
            "status": "disconnected",
            "email": email,
        })


# ======================================================================
# Backup views
# ======================================================================


class BackupStartView(APIView):
    """
    POST /api/gdrive/backup/start
    Start a new backup operation.

    Body: {"backup_type": "settings"|"media"|"full"}
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = StartBackupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        backup_type = serializer.validated_data["backup_type"]
        media_types = serializer.validated_data.get("media_types") or None
        settings_categories = serializer.validated_data.get("settings_categories") or None
        cleanup_after = serializer.validated_data.get("cleanup_after", False)

        # Verify Google Drive is connected
        if not GoogleDriveAccount.objects.filter(is_active=True).exists():
            return Response(
                {"error": "No Google Drive account connected. Please connect first."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Check for already-running backups
        running = BackupRecord.objects.filter(
            status__in=[BackupRecord.Status.PENDING, BackupRecord.Status.RUNNING],
        ).first()
        queued = bool(running)

        # Create record and dispatch task.
        # If another backup is running, this one queues in Celery
        # and starts automatically when the worker is free.
        record = BackupRecord.objects.create(
            backup_type=backup_type,
            status=BackupRecord.Status.PENDING,
        )

        task = run_backup.delay(record.pk, backup_type, media_types=media_types, settings_categories=settings_categories, cleanup_after=cleanup_after)
        record.celery_task_id = task.id
        record.save(update_fields=["celery_task_id"])

        msg = "queued (another backup in progress)" if queued else "started"
        logger.info(
            "Backup %s: id=%d type=%s task=%s",
            msg, record.pk, backup_type, task.id,
        )

        return Response(
            {
                "backup_id": record.pk,
                "backup_type": backup_type,
                "status": "queued" if queued else record.status,
                "celery_task_id": task.id,
                "message": f"Backup {msg}",
            },
            status=status.HTTP_202_ACCEPTED,
        )


class BackupListView(generics.ListAPIView):
    """
    GET /api/gdrive/backup/list
    List all backup records with status and size.
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    serializer_class = BackupRecordSerializer
    queryset = BackupRecord.objects.all()


class BackupDetailView(generics.RetrieveAPIView):
    """
    GET /api/gdrive/backup/{id}
    Get details of a specific backup.
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    serializer_class = BackupRecordDetailSerializer
    queryset = BackupRecord.objects.all()
    lookup_field = "pk"


class BackupDeleteView(APIView):
    """
    DELETE /api/gdrive/backup/{id}
    Delete a backup from both Google Drive and the local database.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def delete(self, request, pk):
        try:
            record = BackupRecord.objects.get(pk=pk)
        except BackupRecord.DoesNotExist:
            return Response(
                {"error": "Backup not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Do not allow deletion of in-progress backups
        if record.status in (BackupRecord.Status.PENDING, BackupRecord.Status.RUNNING):
            return Response(
                {"error": "Cannot delete a backup that is still in progress."},
                status=status.HTTP_409_CONFLICT,
            )

        # Delete from Google Drive if there is a file ID
        if record.gdrive_file_id:
            try:
                gdrive = GoogleDriveService()
                gdrive.load_credentials()
                gdrive.delete_file(record.gdrive_file_id)
            except GoogleDriveServiceError as e:
                logger.warning(
                    "Failed to delete Google Drive file %s: %s (proceeding with DB deletion)",
                    record.gdrive_file_id,
                    e,
                )
            except Exception as e:
                logger.warning(
                    "Unexpected error deleting Google Drive file: %s (proceeding with DB deletion)",
                    e,
                )

        backup_id = record.pk
        record.delete()
        logger.info("Backup %d deleted", backup_id)

        return Response(
            {"status": "deleted", "backup_id": backup_id},
            status=status.HTTP_200_OK,
        )


class BackupCancelView(APIView):
    """
    POST /api/gdrive/backup/{id}/cancel
    Cancel a running or pending backup. Revokes the Celery task,
    cleans up temp files, and marks the record as failed.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, pk):
        try:
            record = BackupRecord.objects.get(pk=pk)
        except BackupRecord.DoesNotExist:
            return Response(
                {"error": "Backup not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if record.status not in (BackupRecord.Status.PENDING, BackupRecord.Status.RUNNING):
            return Response(
                {"error": "Backup is not in progress.", "status": record.status},
                status=status.HTTP_409_CONFLICT,
            )

        # Revoke the Celery task
        if record.celery_task_id:
            try:
                from celery import current_app
                current_app.control.revoke(
                    record.celery_task_id, terminate=True, signal="SIGTERM",
                )
                logger.info(
                    "Revoked celery task %s for backup %d",
                    record.celery_task_id, pk,
                )
            except Exception as e:
                logger.warning("Failed to revoke celery task %s: %s", record.celery_task_id, e)

        record.mark_failed("Cancelled by user")
        logger.info("Backup %d cancelled by user", pk)

        return Response({"status": "cancelled", "backup_id": pk})


class BackupRestoreView(APIView):
    """
    POST /api/gdrive/backup/{id}/restore
    Restore from a specific backup.

    Body: {"confirm": true}
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, pk):
        serializer = RestoreBackupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if not serializer.validated_data.get("confirm"):
            return Response(
                {"error": "You must set 'confirm' to true to proceed with restore."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            record = BackupRecord.objects.get(pk=pk)
        except BackupRecord.DoesNotExist:
            return Response(
                {"error": "Backup not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if record.status != BackupRecord.Status.COMPLETED:
            return Response(
                {"error": "Can only restore from completed backups."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not record.gdrive_file_id:
            return Response(
                {"error": "Backup has no Google Drive file reference."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Verify Google Drive is connected
        if not GoogleDriveAccount.objects.filter(is_active=True).exists():
            return Response(
                {"error": "No Google Drive account connected."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        task = run_restore.delay(record.pk)

        logger.info("Restore started from backup %d, task=%s", record.pk, task.id)

        return Response(
            {
                "status": "restore_started",
                "backup_id": record.pk,
                "backup_type": record.backup_type,
                "celery_task_id": task.id,
            },
            status=status.HTTP_202_ACCEPTED,
        )


# ======================================================================
# Space management
# ======================================================================


class DriveSpaceView(APIView):
    """
    GET /api/gdrive/space
    Returns Google Drive storage info and estimated backup sizes.
    If the token lacks quota permissions (device-code flow),
    returns quota fields as 0 with estimates still populated.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        if not GoogleDriveAccount.objects.filter(is_active=True).exists():
            return Response(
                {"error": "No Google Drive account connected."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Try to get quota (may fail with drive.file scope)
        quota = {
            "total_bytes": 0,
            "used_bytes": 0,
            "available_bytes": 0,
            "total_display": "",
            "used_display": "",
            "available_display": "",
            "usage_percent": 0.0,
        }
        try:
            gdrive = GoogleDriveService()
            gdrive.load_credentials()
            quota = gdrive.get_storage_quota()
        except GoogleDriveServiceError:
            logger.info("Storage quota unavailable (likely drive.file scope)")

        # Estimate backup sizes (local disk scan - always works)
        try:
            estimates = estimate_backup_sizes()
        except Exception as e:
            logger.warning("Failed to estimate backup sizes: %s", e)
            estimates = {
                "estimated_settings_backup_mb": 0.0,
                "estimated_media_backup_mb": 0.0,
                "estimated_recordings_mb": 0.0,
                "estimated_clips_mb": 0.0,
                "estimated_snapshots_mb": 0.0,
            }

        data = {**quota, **estimates}
        serializer = DriveSpaceSerializer(data)
        return Response(serializer.data)


# ======================================================================
# Schedule views
# ======================================================================


class BackupScheduleViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    CRUD ViewSet for backup schedules.

    GET    /api/gdrive/schedule/        - List all schedules
    POST   /api/gdrive/schedule/        - Create a schedule
    GET    /api/gdrive/schedule/{id}/   - Get a schedule
    PUT    /api/gdrive/schedule/{id}/   - Update a schedule
    DELETE /api/gdrive/schedule/{id}/   - Delete a schedule
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    serializer_class = BackupScheduleSerializer
    queryset = BackupSchedule.objects.all()


# ======================================================================
# Portal web page
# ======================================================================


class TestPageView(APIView):
    """
    GET /api/gdrive/test
    Apple-style white-theme portal for Google Drive backup.
    Uses Google Sign-In popup for OAuth (full Drive scopes).
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        from .gdrive_service import GOOGLE_CLIENT_ID
        host = request.get_host()

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <title>SecureProtect Cloud Backup</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display',
               'Segoe UI', Roboto, sans-serif; max-width: 540px; margin: 0 auto;
               padding: 24px 16px; background: #f5f5f7; color: #1d1d1f;
               -webkit-font-smoothing: antialiased; }}
        h1 {{ font-size: 28px; font-weight: 700; letter-spacing: -0.5px; }}
        .sub {{ color: #86868b; font-size: 13px; margin: 4px 0 28px; }}
        .card {{ background: #fff; border-radius: 14px; padding: 20px;
                 margin-bottom: 14px; box-shadow: 0 1px 3px rgba(0,0,0,.06); }}
        .card-title {{ font-size: 13px; font-weight: 600; color: #86868b;
                       text-transform: uppercase; letter-spacing: 0.5px;
                       margin-bottom: 14px; display: flex; align-items: center;
                       justify-content: space-between; }}
        button {{ border: none; border-radius: 10px; cursor: pointer;
                  font-size: 15px; font-weight: 500; padding: 12px 24px;
                  transition: all .15s ease; display: inline-flex;
                  align-items: center; gap: 6px; }}
        button:active {{ transform: scale(.97); }}
        .btn-primary {{ background: #1d1d1f; color: #fff; width: 100%; justify-content: center; }}
        .btn-primary:hover {{ background: #333; }}
        .btn-primary:disabled {{ background: #d1d1d6; cursor: default; transform: none; }}
        .btn-sm {{ font-size: 12px; padding: 6px 12px; border-radius: 8px; }}
        .btn-ghost {{ background: transparent; color: #1d1d1f; padding: 6px 12px;
                      font-size: 12px; border-radius: 8px; font-weight: 600; }}
        .btn-ghost:hover {{ background: #f5f5f7; }}
        .btn-danger {{ background: #fff; color: #ff3b30; border: 1px solid #e5e5e5;
                       font-size: 13px; padding: 8px 16px; }}
        .btn-danger:hover {{ background: #fff5f5; }}
        .btn-danger-sm {{ background: none; color: #ff3b30; font-size: 12px;
                          padding: 4px 10px; border-radius: 6px; }}
        .btn-danger-sm:hover {{ background: #fff5f5; }}
        .dot {{ width: 8px; height: 8px; border-radius: 50%; display: inline-block; }}
        .dot-green {{ background: #34c759; }}
        .dot-red {{ background: #ff3b30; }}
        .dot-pulse {{ animation: pulse 2s ease-in-out infinite; }}
        @keyframes pulse {{ 0%,100% {{ opacity: 1; }} 50% {{ opacity: .4; }} }}
        .connected-row {{ display: flex; align-items: center; gap: 10px; }}
        .connected-email {{ font-size: 16px; font-weight: 500; flex: 1; }}
        .device-code-box {{ text-align: center; padding: 16px 0; }}
        .device-code-url {{ font-size: 13px; color: #86868b; margin-bottom: 12px; }}
        .device-code-url a {{ color: #1d1d1f; text-decoration: underline; }}
        .device-code {{ font-family: 'SF Mono', 'Menlo', monospace; font-size: 32px;
                        font-weight: 700; letter-spacing: 4px; color: #1d1d1f; padding: 12px 0; }}
        .device-code-timer {{ font-size: 12px; color: #86868b; margin-top: 8px;
                              display: flex; align-items: center; justify-content: center; gap: 6px; }}
        .progress-bar-track {{ background: #e5e5e5; border-radius: 4px; height: 6px;
                               overflow: hidden; margin: 10px 0; }}
        .progress-bar-fill {{ background: #1d1d1f; height: 100%; border-radius: 4px;
                              transition: width .3s ease; }}
        .progress-text {{ font-size: 13px; color: #86868b; }}
        .list-item {{ display: flex; align-items: center; padding: 12px 0;
                      border-bottom: 1px solid #f0f0f0; }}
        .list-item:last-child {{ border-bottom: none; }}
        .list-info {{ flex: 1; min-width: 0; }}
        .list-title {{ font-size: 14px; font-weight: 500; }}
        .list-sub {{ font-size: 12px; color: #86868b; }}
        .chip {{ font-size: 11px; padding: 3px 8px; border-radius: 6px; font-weight: 500;
                 white-space: nowrap; margin-left: 8px; }}
        .chip-ok {{ background: #e8f8ee; color: #1b7d3a; }}
        .chip-fail {{ background: #ffeeed; color: #d63031; }}
        .chip-run {{ background: #f0f0f0; color: #1d1d1f; }}
        .space-bar {{ display: flex; align-items: center; gap: 12px; }}
        .space-track {{ flex: 1; background: #e5e5e5; border-radius: 4px; height: 8px; overflow: hidden; }}
        .space-fill {{ height: 100%; border-radius: 4px; transition: width .3s ease; }}
        .space-text {{ font-size: 13px; color: #86868b; }}
        .toast {{ position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
                  background: #1d1d1f; color: #fff; padding: 12px 24px; border-radius: 12px;
                  font-size: 14px; opacity: 0; transition: opacity .3s ease; z-index: 100;
                  pointer-events: none; max-width: 90%; text-align: center; }}
        .toast.show {{ opacity: 1; }}
        .modal-overlay {{ position: fixed; inset: 0; background: rgba(0,0,0,.4);
                          display: flex; align-items: center; justify-content: center; z-index: 99; }}
        .modal {{ background: #fff; border-radius: 16px; padding: 24px;
                  max-width: 360px; width: 90%; }}
        .modal h3 {{ font-size: 17px; margin-bottom: 8px; text-align: center; }}
        .modal p {{ font-size: 13px; color: #86868b; margin-bottom: 18px; text-align: center; }}
        .modal-btns {{ display: flex; gap: 8px; }}
        .modal-btns button {{ flex: 1; }}
        .form-group {{ margin-bottom: 14px; }}
        .form-label {{ font-size: 12px; font-weight: 600; color: #86868b; margin-bottom: 6px;
                       display: block; text-transform: uppercase; letter-spacing: 0.3px; }}
        .form-select, .form-input {{ width: 100%; padding: 10px 12px; border: 1px solid #e5e5e5;
                                     border-radius: 10px; font-size: 15px; font-family: inherit;
                                     background: #fff; color: #1d1d1f; appearance: none;
                                     -webkit-appearance: none; }}
        .form-select {{ background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%2386868b' d='M6 8L1 3h10z'/%3E%3C/svg%3E");
                        background-repeat: no-repeat; background-position: right 12px center; padding-right: 32px; }}
        .form-select:focus, .form-input:focus {{ outline: none; border-color: #1d1d1f; }}
        .backup-option {{ padding: 14px; border: 1px solid #e5e5e5; border-radius: 10px;
                         margin-bottom: 8px; cursor: pointer; transition: all .15s ease; }}
        .backup-option:hover {{ background: #f5f5f7; border-color: #c7c7cc; }}
        .backup-option:active {{ transform: scale(.98); }}
        .backup-option.selected {{ border-color: #1d1d1f; background: #f5f5f7; }}
        .backup-option.selected .bo-radio {{ background: #1d1d1f; border-color: #1d1d1f; }}
        .backup-option.selected .bo-radio::after {{ content: ''; display: block; width: 6px;
            height: 6px; border-radius: 50%; background: #fff; margin: 4px; }}
        .bo-header {{ display: flex; align-items: center; gap: 10px; }}
        .bo-radio {{ width: 18px; height: 18px; border-radius: 50%; border: 2px solid #d1d1d6;
                     flex-shrink: 0; display: flex; align-items: center; justify-content: center;
                     transition: all .15s ease; }}
        .bo-title {{ font-size: 15px; font-weight: 600; flex: 1; }}
        .bo-size {{ font-size: 12px; color: #86868b; background: #f0f0f0; padding: 2px 8px;
                    border-radius: 4px; }}
        .bo-desc {{ font-size: 12px; color: #86868b; margin-top: 4px; padding-left: 28px; }}
        .sched-toggle {{ width: 44px; height: 24px; border-radius: 12px; border: none;
                         cursor: pointer; position: relative; transition: background .2s;
                         flex-shrink: 0; padding: 0; }}
        .sched-toggle.on {{ background: #34c759; }}
        .sched-toggle.off {{ background: #e5e5e5; }}
        .sched-toggle::after {{ content: ''; position: absolute; width: 20px; height: 20px;
                                border-radius: 50%; background: #fff; top: 2px;
                                transition: left .2s; box-shadow: 0 1px 3px rgba(0,0,0,.2); }}
        .sched-toggle.on::after {{ left: 22px; }}
        .sched-toggle.off::after {{ left: 2px; }}
        .empty {{ text-align: center; padding: 20px; color: #86868b; font-size: 14px; }}
        .btn-tab {{ background: #f0f0f0; color: #86868b; font-size: 13px; padding: 8px 16px;
                    border-radius: 8px; font-weight: 600; flex: 1; justify-content: center; }}
        .btn-tab.active {{ background: #1d1d1f; color: #fff; }}
        .btn-restore {{ background: #fff; color: #007aff; border: 1px solid #e5e5e5;
                        font-size: 12px; padding: 4px 10px; border-radius: 6px; margin-left: 4px; }}
        .btn-restore:hover {{ background: #f0f5ff; }}
    </style>
</head>
<body>
    <h1>Cloud Backup</h1>
    <p class="sub">Hub: {host}</p>

    <div class="card">
        <div class="card-title">Connection</div>
        <div id="conn-section"><div class="empty">Checking...</div></div>
    </div>

    <div class="card">
        <div class="card-title">New Backup</div>
        <div id="backup-section">
            <div style="font-size:14px;color:#86868b;margin-bottom:14px">Select what to back up to Google Drive.</div>
            <div style="display:flex;gap:6px;margin-bottom:14px">
                <button class="btn-tab active" id="tab-settings" onclick="switchTab('settings')">Settings</button>
                <button class="btn-tab" id="tab-media" onclick="switchTab('media')">Media</button>
            </div>
            <div id="settings-checkboxes"></div>
            <div id="media-checkboxes" style="display:none"></div>
            <div id="cleanup-row" style="display:none">
                <label style="display:flex;align-items:center;gap:10px;padding:12px 0;cursor:pointer">
                    <input type="checkbox" id="cb-cleanup" style="width:18px;height:18px;accent-color:#ff3b30">
                    <span style="flex:1;font-size:14px;font-weight:500;color:#ff3b30">Delete files after upload</span>
                </label>
            </div>
            <button class="btn-primary" id="start-backup-btn" onclick="confirmBackup()" style="margin-top:8px">Back Up</button>
            <div id="backup-progress"></div>
        </div>
    </div>

    <div class="card">
        <div class="card-title">
            <span>Backup History</span>
        </div>
        <div id="backups-section"><div class="empty">Loading...</div></div>
    </div>

    <div class="card">
        <div class="card-title">
            <span>Schedules</span>
            <button class="btn-ghost btn-sm" onclick="showNewSchedule()">+ New</button>
        </div>
        <div id="schedule-section"><div class="empty">Loading...</div></div>
    </div>

    <div class="card">
        <div class="card-title">Storage</div>
        <div id="space-section"><div class="empty">Connect to view</div></div>
    </div>

    <div class="toast" id="toast"></div>

    <script>
        let pollTimer = null, devicePollTimer = null, countdownTimer = null, restorePollTimer = null;
        let currentTab = 'settings';
        const CLIENT_ID = '{GOOGLE_CLIENT_ID}';
        const SCOPES = 'https://www.googleapis.com/auth/drive.file';

        const SETTINGS_CATS = [
            {{ key: 'cameras', label: 'Cameras', desc: 'Camera connections and RTSP URLs' }},
            {{ key: 'camera_settings', label: 'Camera Settings', desc: 'Detection zones, motion sensitivity' }},
            {{ key: 'camera_zones', label: 'Camera Zones', desc: 'Zone boundaries and rules' }},
            {{ key: 'faces', label: 'Faces', desc: 'Registered faces and embeddings' }},
            {{ key: 'vehicles', label: 'Vehicles', desc: 'Known vehicles and plates' }},
            {{ key: 'alarm_settings', label: 'Alarm', desc: 'Alarm configuration' }},
            {{ key: 'garage_door_settings', label: 'Garage Door', desc: 'Garage automation rules' }},
            {{ key: 'external_devices', label: 'External Devices', desc: 'Linked smart devices' }},
            {{ key: 'meross_accounts', label: 'Meross Accounts', desc: 'Meross cloud accounts' }},
            {{ key: 'meross_devices', label: 'Meross Devices', desc: 'Meross smart plugs/switches' }},
            {{ key: 'backup_schedules', label: 'Backup Schedules', desc: 'Scheduled backup rules' }},
        ];

        function toast(msg, ms) {{
            const t = document.getElementById('toast');
            t.textContent = msg; t.classList.add('show');
            setTimeout(() => t.classList.remove('show'), ms || 3000);
        }}

        function confirm_(title, msg) {{
            return new Promise(resolve => {{
                const ov = document.createElement('div');
                ov.className = 'modal-overlay';
                ov.innerHTML = '<div class="modal"><h3>' + title + '</h3><p>' + msg
                    + '</p><div class="modal-btns">'
                    + '<button id="confirm-cancel" style="background:#f5f5f7;color:#1d1d1f">Cancel</button>'
                    + '<button id="confirm-ok" class="btn-primary" style="padding:10px 16px">Confirm</button>'
                    + '</div></div>';
                ov.querySelector('#confirm-cancel').onclick = () => {{ ov.remove(); resolve(false); }};
                ov.querySelector('#confirm-ok').onclick = () => {{ ov.remove(); resolve(true); }};
                document.body.appendChild(ov);
            }});
        }}

        async function api(path, method, body) {{
            const opts = {{ method: method || 'GET', headers: {{}} }};
            if (body) {{ opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }}
            const res = await fetch(path, opts);
            if (res.status === 204) return {{}};
            const text = await res.text();
            return text ? JSON.parse(text) : {{}};
        }}

        function fmtSize(mb) {{
            if (!mb || mb <= 0) return '--';
            if (mb < 1) return '< 1 MB';
            if (mb < 1024) return mb.toFixed(1) + ' MB';
            return (mb / 1024).toFixed(1) + ' GB';
        }}

        function switchTab(tab) {{
            currentTab = tab;
            document.getElementById('tab-settings').className = 'btn-tab' + (tab === 'settings' ? ' active' : '');
            document.getElementById('tab-media').className = 'btn-tab' + (tab === 'media' ? ' active' : '');
            document.getElementById('settings-checkboxes').style.display = tab === 'settings' ? '' : 'none';
            document.getElementById('media-checkboxes').style.display = tab === 'media' ? '' : 'none';
            document.getElementById('cleanup-row').style.display = tab === 'media' ? '' : 'none';
        }}

        function buildSettingsCheckboxes() {{
            const box = document.getElementById('settings-checkboxes');
            if (!box) return;
            box.innerHTML = SETTINGS_CATS.map(s =>
                '<label style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid #f0f0f0;cursor:pointer">'
                + '<input type="checkbox" data-cat="' + s.key + '" checked style="width:18px;height:18px;accent-color:#1d1d1f">'
                + '<div style="flex:1"><div style="font-size:14px;font-weight:500">' + s.label + '</div>'
                + '<div style="font-size:12px;color:#86868b">' + s.desc + '</div></div>'
                + '</label>'
            ).join('');
        }}

        // ---- Connection ----
        async function checkStatus() {{
            try {{
                const data = await api('/api/gdrive/auth/status');
                const el = document.getElementById('conn-section');
                if (data.connected) {{
                    el.innerHTML = '<div class="connected-row">'
                        + '<span class="dot dot-green"></span>'
                        + '<span class="connected-email">' + data.email + '</span>'
                        + '<button class="btn-danger" onclick="disconnect()">Disconnect</button></div>';
                    loadAll();
                }} else {{
                    el.innerHTML = '<button class="btn-primary" onclick="connectGoogle()">Connect Google Drive</button>';
                }}
            }} catch (e) {{
                document.getElementById('conn-section').innerHTML = '<div class="empty">Hub unreachable</div>';
            }}
        }}

        function loadAll() {{ buildSettingsCheckboxes(); loadBackups(); loadSpace(); loadSizes(); loadSchedules(); }}

        // Device Code flow — the only auth method that works with
        // "TVs and Limited Input devices" OAuth client type.
        // User goes to google.com/device and enters a code.
        async function connectGoogle() {{
            const el = document.getElementById('conn-section');
            el.innerHTML = '<div class="empty">Starting sign-in...</div>';
            try {{
                const data = await api('/api/gdrive/auth/device-start', 'POST', {{}});
                if (data.error) {{ toast(data.error, 5000); checkStatus(); return; }}
                el.innerHTML = '<div class="device-code-box">'
                    + '<div class="device-code-url">Go to <a href="' + data.verification_uri + '" target="_blank">'
                    + data.verification_uri + '</a> and enter:</div>'
                    + '<div class="device-code">' + data.user_code + '</div>'
                    + '<div class="device-code-timer"><span class="dot dot-pulse" style="background:#ff9500"></span>'
                    + '<span id="dc-timer">Waiting for sign-in...</span></div></div>';
                pollDeviceCode(data.expires_in);
            }} catch (e) {{
                toast('Error: ' + e.message, 5000);
                checkStatus();
            }}
        }}

        function pollDeviceCode(expiresIn) {{
            if (devicePollTimer) clearInterval(devicePollTimer);
            let elapsed = 0;
            devicePollTimer = setInterval(async () => {{
                elapsed += 5;
                if (elapsed > expiresIn) {{
                    clearInterval(devicePollTimer); devicePollTimer = null;
                    document.getElementById('dc-timer').textContent = 'Code expired. Try again.';
                    setTimeout(checkStatus, 2000);
                    return;
                }}
                try {{
                    const data = await api('/api/gdrive/auth/device-poll');
                    if (data.status === 'connected') {{
                        clearInterval(devicePollTimer); devicePollTimer = null;
                        if (data.has_drive_scope === false) {{
                            toast('Connected but missing Drive permission — backup may fail', 7000);
                        }} else {{
                            toast('Connected as ' + (data.email || ''));
                        }}
                        checkStatus();
                    }} else if (data.status === 'expired' || data.status === 'error') {{
                        clearInterval(devicePollTimer); devicePollTimer = null;
                        toast(data.error || 'Sign-in failed', 5000);
                        checkStatus();
                    }}
                }} catch (e) {{}}
            }}, 5000);
        }}

        async function disconnect() {{
            if (!(await confirm_('Disconnect?', 'Backups on Drive won\\'t be deleted. You can reconnect later.'))) return;
            try {{
                await api('/api/gdrive/auth/disconnect', 'POST', {{}});
                toast('Disconnected');
                ['backups-section','space-section','schedule-section'].forEach(id =>
                    document.getElementById(id).innerHTML = '<div class="empty">Connect to view</div>');
                checkStatus();
            }} catch (e) {{ toast('Error: ' + e.message, 5000); }}
        }}

        // ---- Backup ----
        function getSelectedTypes() {{
            return Array.from(document.querySelectorAll('#media-checkboxes input[type=checkbox]:checked'))
                .map(cb => cb.dataset.type);
        }}

        function getSelectedCategories() {{
            return Array.from(document.querySelectorAll('#settings-checkboxes input[type=checkbox]:checked'))
                .map(cb => cb.dataset.cat);
        }}

        async function confirmBackup() {{
            const btn = document.getElementById('start-backup-btn');
            let body, msg;

            if (currentTab === 'settings') {{
                const cats = getSelectedCategories();
                if (!cats.length) {{ toast('Select at least one category', 3000); return; }}
                const labels = cats.map(c => {{
                    const found = SETTINGS_CATS.find(s => s.key === c);
                    return found ? found.label : c;
                }}).join(', ');
                msg = 'Back up settings to Google Drive:<br><span style="font-size:12px;color:#86868b">' + labels + '</span>';
                body = {{ backup_type: 'settings', settings_categories: cats }};
            }} else {{
                const types = getSelectedTypes();
                if (!types.length) {{ toast('Select at least one category', 3000); return; }}
                const cleanup = document.getElementById('cb-cleanup').checked;
                const nameMap = {{ recordings: 'continuous recordings', clips: 'event videos', snapshots: 'snapshots' }};
                const labels = types.map(t => nameMap[t] || t).join(', ');
                msg = 'Back up ' + labels + ' to Google Drive.';
                if (cleanup) msg += '<br><br><span style="color:#ff3b30;font-weight:600">Local files will be deleted after upload.</span>';
                body = {{ backup_type: 'media', media_types: types }};
                if (cleanup) body.cleanup_after = true;
            }}

            if (!(await confirm_('Start Backup?', msg))) return;
            btn.disabled = true; btn.textContent = 'Starting...';
            try {{
                const data = await api('/api/gdrive/backup/start', 'POST', body);
                if (data.error) {{ toast(data.error, 5000); btn.disabled = false; btn.textContent = 'Back Up'; return; }}
                if (data.backup_id) {{
                    toast(data.message || 'Backup started'); btn.style.display = 'none';
                    pollBackup(data.backup_id);
                }}
            }} catch (e) {{ toast('Error: ' + e.message, 5000); btn.disabled = false; btn.textContent = 'Back Up'; }}
        }}

        function pollBackup(id) {{
            if (pollTimer) clearInterval(pollTimer);
            const prog = document.getElementById('backup-progress');
            pollTimer = setInterval(async () => {{
                try {{
                    const d = await api('/api/gdrive/backup/' + id);
                    let pct = d.progress_total > 0 ? Math.round(d.progress_current / d.progress_total * 100) : 0;
                    const phase = d.progress_phase || d.status;
                    prog.innerHTML = '<div class="progress-bar-track"><div class="progress-bar-fill" style="width:'
                        + pct + '%"></div></div>'
                        + '<div style="display:flex;align-items:center;justify-content:space-between;margin-top:6px">'
                        + '<div class="progress-text">' + phase
                        + (d.progress_total > 0 ? ' (' + d.progress_current + '/' + d.progress_total + ')' : '') + '</div>'
                        + '<button class="btn-danger" style="font-size:12px;padding:6px 14px" onclick="cancelBackup(' + id + ')">Stop</button>'
                        + '</div>';
                    if (d.status === 'completed') {{
                        clearInterval(pollTimer); pollTimer = null;
                        prog.innerHTML = '<div class="progress-text" style="color:#34c759;margin-top:10px">'
                            + 'Completed' + (d.size_display ? ' (' + d.size_display + ')' : '') + '</div>';
                        toast('Backup completed'); loadBackups();
                        const btn = document.getElementById('start-backup-btn');
                        btn.style.display = ''; btn.disabled = false; btn.textContent = 'Back Up';
                    }} else if (d.status === 'failed') {{
                        clearInterval(pollTimer); pollTimer = null;
                        const isCancelled = (d.error_message || '').indexOf('Cancelled') >= 0;
                        prog.innerHTML = '<div class="progress-text" style="color:#ff3b30;margin-top:10px">'
                            + (isCancelled ? 'Backup cancelled' : 'Failed: ' + (d.error_message || 'Unknown')) + '</div>';
                        toast(isCancelled ? 'Backup cancelled' : 'Backup failed', 5000);
                        const btn = document.getElementById('start-backup-btn');
                        btn.style.display = ''; btn.disabled = false; btn.textContent = 'Back Up';
                    }}
                }} catch (e) {{}}
            }}, 3000);
        }}

        async function cancelBackup(id) {{
            if (!(await confirm_('Stop Backup?', 'The backup will be cancelled and any temp files cleaned up. Files already uploaded to Drive will remain.'))) return;
            try {{
                const data = await api('/api/gdrive/backup/' + id + '/cancel', 'POST', {{}});
                if (data.error) {{ toast(data.error, 5000); return; }}
                toast('Cancelling backup...');
            }} catch (e) {{ toast('Error: ' + e.message, 5000); }}
        }}

        // ---- Backup history ----
        async function loadBackups() {{
            try {{
                const data = await api('/api/gdrive/backup/list');
                const el = document.getElementById('backups-section');
                const list = Array.isArray(data) ? data : (data.results || []);
                if (!list.length) {{ el.innerHTML = '<div class="empty">No backups yet</div>'; return; }}
                el.innerHTML = list.slice(0, 20).map(b => {{
                    const chip = b.status === 'completed' ? 'chip-ok' : b.status === 'failed' ? 'chip-fail' : 'chip-run';
                    const d = new Date(b.created_at);
                    const dateStr = d.toLocaleDateString(undefined, {{month:'short',day:'numeric'}})
                        + ' ' + d.toLocaleTimeString(undefined, {{hour:'2-digit',minute:'2-digit'}});
                    const canDel = b.status === 'completed' || b.status === 'failed';
                    const canRestore = b.status === 'completed' && b.gdrive_file_id;
                    return '<div class="list-item"><div class="list-info"><div class="list-title">'
                        + b.backup_type.charAt(0).toUpperCase() + b.backup_type.slice(1)
                        + (b.size_display && b.size_display !== '0 B' ? ' (' + b.size_display + ')' : '')
                        + '</div><div class="list-sub">' + dateStr + '</div></div>'
                        + '<span class="chip ' + chip + '">' + b.status + '</span>'
                        + (canRestore ? '<button class="btn-restore" onclick="restoreBackup(' + b.id + ',\\\'' + b.backup_type + '\\\')">Restore</button>' : '')
                        + (canDel ? '<button class="btn-danger-sm" onclick="deleteBackup(' + b.id + ')">Delete</button>' : '')
                        + '</div>';
                }}).join('');
            }} catch (e) {{
                document.getElementById('backups-section').innerHTML = '<div class="empty">Error loading</div>';
            }}
        }}

        async function deleteBackup(id) {{
            if (!(await confirm_('Delete Backup?', 'This will remove the backup from Google Drive and cannot be undone.'))) return;
            try {{
                await api('/api/gdrive/backup/' + id + '/delete', 'DELETE');
                toast('Backup deleted'); loadBackups();
            }} catch (e) {{ toast('Error: ' + e.message, 5000); }}
        }}

        async function restoreBackup(id, type) {{
            const typeLabel = type.charAt(0).toUpperCase() + type.slice(1);
            const msg = 'Restore <strong>' + typeLabel + '</strong> from this backup?'
                + '<br><span style="font-size:12px;color:#86868b">'
                + (type === 'settings' ? 'Existing settings will be updated with backup values.'
                   : type === 'media' ? 'Media files will be restored to their original location.'
                   : 'Settings and media will be restored.')
                + '</span>';
            if (!(await confirm_('Restore Backup?', msg))) return;
            try {{
                const data = await api('/api/gdrive/backup/' + id + '/restore', 'POST', {{ confirm: true }});
                if (data.error) {{ toast(data.error, 5000); return; }}
                toast('Restore started...');
                pollRestore(id);
            }} catch (e) {{ toast('Error: ' + e.message, 5000); }}
        }}

        function pollRestore(id) {{
            if (restorePollTimer) clearInterval(restorePollTimer);
            const el = document.getElementById('backups-section');
            const origHtml = el.innerHTML;
            el.innerHTML = '<div style="padding:16px;text-align:center">'
                + '<div class="dot dot-pulse" style="background:#007aff;width:10px;height:10px;display:inline-block;margin-bottom:8px"></div>'
                + '<div style="font-size:14px;font-weight:500">Restoring...</div>'
                + '<div style="font-size:12px;color:#86868b" id="restore-status">Downloading from Google Drive</div></div>';
            let checks = 0;
            restorePollTimer = setInterval(async () => {{
                checks++;
                if (checks > 120) {{ // 10 min timeout
                    clearInterval(restorePollTimer); restorePollTimer = null;
                    toast('Restore timed out', 5000); loadBackups();
                    return;
                }}
                try {{
                    const d = await api('/api/gdrive/backup/' + id);
                    // Restore doesn't update the backup record status,
                    // so we just poll for a bit then declare success
                    const statusEl = document.getElementById('restore-status');
                    if (checks < 3) {{
                        if (statusEl) statusEl.textContent = 'Downloading from Google Drive...';
                    }} else if (checks < 8) {{
                        if (statusEl) statusEl.textContent = 'Applying restore...';
                    }} else {{
                        clearInterval(restorePollTimer); restorePollTimer = null;
                        toast('Restore completed'); loadBackups();
                    }}
                }} catch (e) {{
                    clearInterval(restorePollTimer); restorePollTimer = null;
                    toast('Restore may have completed — check logs', 5000); loadBackups();
                }}
            }}, 5000);
        }}

        // ---- Space + Sizes ----
        function fmtDuration(mb) {{
            // Rough estimate: 5 Mbps upload = 0.625 MB/s
            if (!mb || mb <= 0) return null;
            const secs = mb / 0.625;
            if (secs < 60) return 'under a minute';
            if (secs < 3600) return Math.ceil(secs / 60) + ' minutes';
            const hrs = Math.floor(secs / 3600);
            const mins = Math.ceil((secs % 3600) / 60);
            return hrs + 'h ' + mins + 'm';
        }}

        async function loadSpace() {{
            try {{
                const data = await api('/api/gdrive/space');
                if (data.error) {{
                    document.getElementById('space-section').innerHTML = '<div class="empty">Connect to view</div>';
                    return;
                }}
                const el = document.getElementById('space-section');
                let html = '';

                // Google Drive quota (if available)
                if (data.total_bytes > 0) {{
                    const pct = data.usage_percent || 0;
                    const color = pct > 90 ? '#ff3b30' : pct > 70 ? '#ff9500' : '#34c759';
                    html += '<div style="font-size:13px;font-weight:600;margin-bottom:8px">Google Drive</div>'
                        + '<div class="space-bar"><div class="space-track"><div class="space-fill" style="width:'
                        + pct + '%;background:' + color + '"></div></div><div class="space-text">' + pct + '%</div></div>'
                        + '<div class="space-text" style="margin-top:4px">' + (data.used_display||'?')
                        + ' of ' + (data.total_display||'?') + ' used (' + (data.available_display||'?') + ' available)</div>';
                }}

                // Local media estimate
                const mediaMb = data.estimated_media_backup_mb || 0;
                if (mediaMb > 0) {{
                    const sizeStr = fmtSize(mediaMb);
                    const timeStr = fmtDuration(mediaMb);
                    html += '<div style="margin-top:12px;font-size:13px;font-weight:600;margin-bottom:4px">Media on this hub</div>'
                        + '<div class="space-text">' + sizeStr + ' of recordings, clips, and snapshots</div>';
                    if (timeStr) html += '<div class="space-text">Estimated backup time: ~' + timeStr + '</div>';
                }}

                // Build media checkboxes — only show types that have data
                const types = [
                    {{ key: 'recordings', label: 'Continuous Recordings', desc: '24/7 camera footage', mb: data.estimated_recordings_mb || 0 }},
                    {{ key: 'clips',      label: 'Event Videos',         desc: 'Motion & detection clips', mb: data.estimated_clips_mb || 0 }},
                    {{ key: 'snapshots',  label: 'Snapshots',            desc: 'Event still images',       mb: data.estimated_snapshots_mb || 0 }},
                ];
                const cbBox = document.getElementById('media-checkboxes');
                if (cbBox) {{
                    cbBox.innerHTML = types.filter(t => t.mb > 0).map(t =>
                        '<label style="display:flex;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid #f0f0f0;cursor:pointer">'
                        + '<input type="checkbox" data-type="' + t.key + '" checked style="width:18px;height:18px;accent-color:#1d1d1f">'
                        + '<div style="flex:1"><div style="font-size:15px;font-weight:500">' + t.label + '</div>'
                        + '<div style="font-size:12px;color:#86868b">' + t.desc + '</div></div>'
                        + '<span class="bo-size">' + fmtSize(t.mb) + '</span>'
                        + '</label>'
                    ).join('');
                    if (!cbBox.innerHTML) cbBox.innerHTML = '<div class="empty">No media files on this hub</div>';
                }}

                el.innerHTML = html || '<div class="empty">No data</div>';
            }} catch (e) {{
                document.getElementById('space-section').innerHTML = '<div class="empty">Unable to load</div>';
            }}
        }}

        function loadSizes() {{ /* handled by loadSpace */ }}

        // ---- Schedules ----
        async function loadSchedules() {{
            try {{
                const data = await api('/api/gdrive/schedule/');
                const el = document.getElementById('schedule-section');
                const list = Array.isArray(data) ? data : (data.results || []);
                if (!list.length) {{ el.innerHTML = '<div class="empty">No schedules yet</div>'; return; }}
                const days = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
                el.innerHTML = list.map(s => {{
                    const freq = s.frequency.charAt(0).toUpperCase() + s.frequency.slice(1);
                    const type = s.backup_type.charAt(0).toUpperCase() + s.backup_type.slice(1);
                    const time = s.time_of_day ? utcTimeToLocal(s.time_of_day) : '';
                    let detail = type + ' backup at ' + time;
                    if (s.frequency === 'weekly' && s.day_of_week !== null) detail += ' on ' + (days[s.day_of_week]||'');
                    if (s.frequency === 'monthly' && s.day_of_month) detail += ' on day ' + s.day_of_month;
                    return '<div class="list-item">'
                        + '<div class="list-info"><div class="list-title">' + freq + '</div>'
                        + '<div class="list-sub">' + detail + '</div></div>'
                        + '<button class="sched-toggle ' + (s.is_enabled ? 'on' : 'off')
                        + '" onclick="toggleSchedule(' + s.id + ',' + !s.is_enabled + ')"></button>'
                        + '<button class="btn-danger-sm" onclick="deleteSchedule(' + s.id + ')">Delete</button>'
                        + '</div>';
                }}).join('');
            }} catch (e) {{
                document.getElementById('schedule-section').innerHTML = '<div class="empty">Error loading</div>';
            }}
        }}

        async function toggleSchedule(id, enable) {{
            try {{
                await api('/api/gdrive/schedule/' + id + '/', 'PATCH', {{ is_enabled: enable }});
                toast(enable ? 'Schedule enabled' : 'Schedule disabled'); loadSchedules();
            }} catch (e) {{ toast('Error: ' + e.message, 5000); }}
        }}

        async function deleteSchedule(id) {{
            if (!(await confirm_('Delete Schedule?', 'This schedule will be permanently removed.'))) return;
            try {{
                await api('/api/gdrive/schedule/' + id + '/', 'DELETE');
                toast('Schedule deleted'); loadSchedules();
            }} catch (e) {{ toast('Error: ' + e.message, 5000); }}
        }}

        function showNewSchedule() {{
            const ov = document.createElement('div');
            ov.className = 'modal-overlay';
            ov.innerHTML = '<div class="modal"><h3>New Schedule</h3>'
                + '<div class="form-group"><label class="form-label">Backup Type</label>'
                + '<select class="form-select" id="ns-type"><option value="settings">Settings</option><option value="media">Media</option><option value="full">Full (Settings + Media)</option></select></div>'
                + '<div class="form-group"><label class="form-label">Frequency</label>'
                + '<select class="form-select" id="ns-freq" onchange="onFreqChange()">'
                + '<option value="daily">Daily</option><option value="weekly">Weekly</option>'
                + '<option value="monthly">Monthly</option></select></div>'
                + '<div class="form-group"><label class="form-label">Time</label>'
                + '<input type="time" class="form-input" id="ns-time" value="03:00"></div>'
                + '<div class="form-group" id="ns-dow-group" style="display:none">'
                + '<label class="form-label">Day of Week</label>'
                + '<select class="form-select" id="ns-dow">'
                + '<option value="0">Monday</option><option value="1">Tuesday</option>'
                + '<option value="2">Wednesday</option><option value="3">Thursday</option>'
                + '<option value="4">Friday</option><option value="5">Saturday</option>'
                + '<option value="6">Sunday</option></select></div>'
                + '<div class="form-group" id="ns-dom-group" style="display:none">'
                + '<label class="form-label">Day of Month</label>'
                + '<select class="form-select" id="ns-dom">'
                + Array.from({{length:28}}, (_,i) => '<option value="'+(i+1)+'">'+(i+1)+'</option>').join('')
                + '</select></div>'
                + '<div class="modal-btns">'
                + '<button style="background:#f5f5f7;color:#1d1d1f" onclick="this.closest(\\'.modal-overlay\\').remove()">Cancel</button>'
                + '<button class="btn-primary" style="padding:10px 16px" onclick="createSchedule(this.closest(\\'.modal-overlay\\'))">Create</button>'
                + '</div></div>';
            document.body.appendChild(ov);
        }}

        function onFreqChange() {{
            const f = document.getElementById('ns-freq').value;
            document.getElementById('ns-dow-group').style.display = f === 'weekly' ? '' : 'none';
            document.getElementById('ns-dom-group').style.display = f === 'monthly' ? '' : 'none';
        }}

        function localTimeToUtc(timeStr) {{
            // Convert "HH:MM" local time to UTC "HH:MM:SS"
            const [h, m] = timeStr.split(':').map(Number);
            const now = new Date();
            now.setHours(h, m, 0, 0);
            const utcH = String(now.getUTCHours()).padStart(2, '0');
            const utcM = String(now.getUTCMinutes()).padStart(2, '0');
            return utcH + ':' + utcM + ':00';
        }}

        function utcTimeToLocal(timeStr) {{
            // Convert "HH:MM:SS" UTC to local "HH:MM"
            const [h, m] = timeStr.split(':').map(Number);
            const d = new Date();
            d.setUTCHours(h, m, 0, 0);
            return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
        }}

        async function createSchedule(overlay) {{
            const body = {{
                backup_type: document.getElementById('ns-type').value,
                frequency: document.getElementById('ns-freq').value,
                time_of_day: localTimeToUtc(document.getElementById('ns-time').value),
            }};
            if (body.frequency === 'weekly') body.day_of_week = parseInt(document.getElementById('ns-dow').value);
            if (body.frequency === 'monthly') body.day_of_month = parseInt(document.getElementById('ns-dom').value);
            try {{
                const data = await api('/api/gdrive/schedule/', 'POST', body);
                if (data.id) {{ overlay.remove(); toast('Schedule created'); loadSchedules(); }}
                else {{ toast(JSON.stringify(data), 5000); }}
            }} catch (e) {{ toast('Error: ' + e.message, 5000); }}
        }}

        // Init
        buildSettingsCheckboxes();
        checkStatus();
    </script>
</body>
</html>"""

        return DjangoHttpResponse(html, content_type="text/html")
