from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from parcel_detect.serializers import ParcelDetectSerializer
from parcel_detect.tasks import update_parcel_detect_config


class ParcelDetectView(GenericAPIView):
    serializer_class = ParcelDetectSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)

        if serializer.is_valid():
            data = serializer.validated_data
            update_parcel_detect_config.apply_async(
                args=(data,), queue="update_parcel_queue"
            )
            return Response(status=status.HTTP_202_ACCEPTED)
        return Response({"message": serializer.errors})
