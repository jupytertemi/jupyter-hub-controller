import logging
import json
import subprocess

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from hub_operations.serializers import ConfirmSerializer
from hub_operations.services import delete_hub_request
from hub_operations.tasks import hub_reset_task
from utils.restarting_service import restart_service
from utils.update_env import read_env_file


class RestartingView(GenericAPIView):
    serializer_class = ConfirmSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if serializer.validated_data["confirm"]:
            try:
                # Run the `sudo reboot` command
                subprocess.run(["sudo", "reboot"], check=True)
            except subprocess.CalledProcessError as err:
                return Response(
                    {"error": str(err)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
            return Response(
                {"message": "Reboot initiated"}, status=status.HTTP_202_ACCEPTED
            )

        return Response(
            {"message": "Reboot cancelled"}, status=status.HTTP_400_BAD_REQUEST
        )


class ResettingView(GenericAPIView):
    serializer_class = ConfirmSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if serializer.validated_data["confirm"]:
            # Attempt cloud deregistration but don't block on failure
            try:
                delete_hub = delete_hub_request()
                if not delete_hub:
                    logging.warning("Cloud hub delete failed — proceeding with local reset anyway")
            except Exception as e:
                logging.warning(f"Cloud hub delete error: {e} — proceeding with local reset anyway")

            hub_reset_task.apply_async(
                queue="hub_operations_queue", countdown=5
            )
            return Response(
                {"message": "Reset success"}, status=status.HTTP_202_ACCEPTED
            )
        return Response({"message": "Reset fail"}, status=status.HTTP_400_BAD_REQUEST)


class RestartCloudflaredView(GenericAPIView):
    serializer_class = ConfirmSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if serializer.validated_data["confirm"]:
            try:
                # Run the `sudo systemctl restart cloudflared-tunnel.service` command
                subprocess.run(
                    ["systemctl", "restart", "cloudflared-tunnel.service"], check=True
                )
            except subprocess.CalledProcessError as err:
                return Response(
                    {"error": str(err)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )

            try:
                # restart docker container video sever
                restart_service("video_server")
            except Exception as err:
                return Response(
                    {"error": str(err)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )

            return Response(
                {"message": "Restart cloudflared tunnel success"},
                status=status.HTTP_202_ACCEPTED,
            )

        return Response(
            {"message": "Restart cloudflared tunnel cancelled"},
            status=status.HTTP_400_BAD_REQUEST,
        )


class OnboardingStatusView(GenericAPIView):
    """GET endpoint for Flutter to confirm hub onboarding is complete.
    No auth required — called during/after BLE onboarding before JWT exists.

    Flutter should poll this every 2s on the LOCAL IP after BLE sends "done".
    When ready=true, show 100% and redirect to home screen.

    Response:
        {"ready": false, "status": "setup_mode"}     — not onboarded
        {"ready": true,  "status": "onboarded", "device_name": "...", "hub_user_id": 2}
    """
    permission_classes = []
    authentication_classes = []

    def get(self, request):
        hub_user_id = read_env_file("HUB_USER_ID")
        device_name = read_env_file("DEVICE_NAME")

        if not hub_user_id or hub_user_id == "0" or not device_name:
            return Response(
                {"ready": False, "status": "setup_mode"},
                status=status.HTTP_200_OK,
            )

        return Response(
            {
                "ready": True,
                "status": "onboarded",
                "device_name": device_name,
                "hub_user_id": int(hub_user_id),
            },
            status=status.HTTP_200_OK,
        )


class ResettingProgressView(GenericAPIView):
    """GET endpoint for real-time reset progress.
    Reads /tmp/jupyter-hub-progress.json written by reset_hub.sh.
    No auth required — reset tears down auth infrastructure."""
    permission_classes = []
    authentication_classes = []

    def get(self, request):
        progress_file = "/tmp/jupyter-hub-progress.json"
        try:
            with open(progress_file, "r") as f:
                data = json.load(f)
            return Response(data, status=status.HTTP_200_OK)
        except FileNotFoundError:
            return Response(
                {"operation": "unknown", "step": 0, "total_steps": 8, "percent": 0, "status": "not_started", "message": "No operation in progress"},
                status=status.HTTP_200_OK,
            )
        except json.JSONDecodeError:
            return Response(
                {"operation": "unknown", "step": 0, "total_steps": 8, "percent": 0, "status": "error", "message": "Progress file corrupt"},
                status=status.HTTP_200_OK,
            )
