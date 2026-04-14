import subprocess

from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from hub_operations.serializers import ConfirmSerializer
from hub_operations.services import delete_hub_request
from hub_operations.tasks import hub_reset_task
from utils.restarting_service import restart_service


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
            try:
                delete_hub = delete_hub_request()
                if delete_hub:
                    hub_reset_task.apply_async(
                        queue="hub_operations_queue", countdown=5
                    )
                    return Response(
                        {"message": "Reset success"}, status=status.HTTP_202_ACCEPTED
                    )
            except Exception as e:
                return Response(
                    {"message": f"Error {e}"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
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
