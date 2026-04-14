from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from loitering.serializers import LoiteringSerializer
from loitering.tasks import update_loitering_config


class LoiteringView(GenericAPIView):
    serializer_class = LoiteringSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            restrictive_zone = serializer.validated_data["restrictive_zone"]
            update_loitering_config.apply_async(
                args=(restrictive_zone,), queue="loitering_queue"
            )
            return Response(status=status.HTTP_202_ACCEPTED)
        return Response({"message": serializer.errors})
