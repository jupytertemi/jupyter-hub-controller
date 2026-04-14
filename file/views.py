import json
import subprocess

from django.conf import settings
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from file.serializers import (
    CreatePlayBackSessionSerializer,
    CreateTransferSessionSerializer,
    TransferType,
)
from utils.upload_file import UploadFileHandler


class BaseTransferSessionView(GenericAPIView):
    serializer_class = None
    handler = None
    transfer_type = None

    def post(self, request):
        serializer = self.serializer_class(data=request.data)

        if serializer.is_valid():
            handler = self.get_handler(serializer)

            self.start_webrtc_session(
                handler,
                serializer.validated_data["file_url"],
                serializer.data["session_id"],
                serializer.data["ice_servers"],
            )

            return Response(status=status.HTTP_202_ACCEPTED)

        return Response({"message": serializer.errors})

    def get_handler(self, serializer):
        """Subclasses will have to override this method."""
        raise NotImplementedError("Subclass must implement get_handler method.")

    def start_webrtc_session(self, handler, file_url, session_id, ice_servers):
        subprocess.Popen(
            [
                "python",
                handler,
                file_url,
                settings.MQTT_HOST,
                str(settings.MQTT_PORT),
                settings.MQTT_USERNAME,
                settings.MQTT_PASSWORD,
                session_id,
                json.dumps(ice_servers),
            ]
        )


class CreateTransferSessionView(BaseTransferSessionView):
    serializer_class = CreateTransferSessionSerializer

    def get_handler(self, serializer):
        return (
            settings.WEBRTC_FILE_SENDER_PATH
            if serializer.data["type"] == TransferType.RECEIVE.value
            else settings.WEBRTC_FILE_RECEIVER_PATH
        )


class CreatePlayBackSessionView(BaseTransferSessionView):
    serializer_class = CreatePlayBackSessionSerializer

    def get_handler(self, serializer):
        return settings.WEBRTC_PLAY_BACK_VIDEO_PATH


class FileUploadView(APIView):
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request, *args, **kwargs):
        if "file" not in request.FILES:
            return Response(
                {"error": "No file uploaded"}, status=status.HTTP_400_BAD_REQUEST
            )
        uploaded_file = request.FILES["file"]
        upload_folder = settings.BASE_DIR_FILE
        safe_filename = UploadFileHandler(uploaded_file, upload_folder).save_file()
        file_url = request.build_absolute_uri(f"{settings.STATIC_URL}{safe_filename}")

        return Response({"file_path": file_url}, status=status.HTTP_201_CREATED)
