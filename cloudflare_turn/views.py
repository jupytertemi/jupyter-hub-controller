from rest_framework.generics import ListCreateAPIView
from rest_framework.response import Response

from cloudflare_turn.models import Turn
from cloudflare_turn.serializers import TurnSerializer
from cloudflare_turn.tasks import hub_auto_restart_cloudflare_token
from core.pagination import Pagination


class ListCreateTurnView(ListCreateAPIView):
    serializer_class = TurnSerializer
    pagination_class = Pagination
    queryset = Turn.objects.order_by("-created_at")

    def list(self, request, *args, **kwargs):
        turn = self.get_queryset().first()

        if not turn:
            hub_auto_restart_cloudflare_token()
            turn = self.get_queryset().first()

        serializer = self.get_serializer(turn)
        return Response(serializer.data)
