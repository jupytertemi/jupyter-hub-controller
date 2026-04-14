from drf_yasg.utils import swagger_auto_schema
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from logbook.serializers import GetLogbookEntitySerializer
from utils.hass_client import InterfaceHASSView


class GetLogbookBaseView(InterfaceHASSView):
    def fetch_logbook(self, entity_id=None):
        client = self.getHassClient()
        query = self.request.GET
        start_time = query.get("start_time")
        end_time = query.get("end_time")

        try:
            return client.get_logbook(start_time, end_time, entity_id)
        except Exception as err:
            raise ValidationError({"error": str(err)})


class GetLogbookView(GetLogbookBaseView):
    serializer_class = GetLogbookEntitySerializer

    @swagger_auto_schema(
        query_serializer=GetLogbookEntitySerializer(),
    )
    def get(self, request, *args, **kwargs):
        entry = self.fetch_logbook()
        return Response(data=entry, status=200)


class GetLogbookEntityView(GetLogbookBaseView):
    serializer_class = GetLogbookEntitySerializer

    @swagger_auto_schema(
        query_serializer=GetLogbookEntitySerializer(),
    )
    def get(self, request, entity_id, *args, **kwargs):
        entry = self.fetch_logbook(entity_id)
        return Response(data=entry, status=200)
