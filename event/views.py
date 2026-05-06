from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import CustomCursorPagination
from event.filter import EventFilter
from event.models import Event
from event.serializers import (
    EventDetailSerializer,
    EventSerializer,
    EventVerdictSerializer,
)


class ListEventView(ListAPIView):
    model = Event
    serializer_class = EventSerializer
    queryset = Event.objects.all()
    pagination_class = CustomCursorPagination
    filter_backends = [DjangoFilterBackend, OrderingFilter, SearchFilter]
    search_fields = ["title"]
    filterset_class = EventFilter


class RetrieveEventView(RetrieveAPIView):
    model = Event
    serializer_class = EventDetailSerializer
    queryset = Event.objects.all()
    lookup_field = "event_id"

    def get_object(self):
        event_id = self.kwargs["event_id"]
        try:
            return self.get_queryset().get(event_id=event_id)
        except (ValueError, TypeError):
            raise NotFound(detail="Invalid Event ID.")
        except Event.DoesNotExist:
            raise NotFound(detail="Event not found.")


class ListCountEventView(ListEventView):
    def get(self, request, *args, **kwargs):
        get_queryset = self.get_queryset()
        queryset = self.filter_queryset(get_queryset)
        responses = self.model.objects.counts(queryset)
        return Response({"results": responses})


class EventVerdictView(APIView):
    """PATCH /api/events/{event_id}/verdict (Helios Tier 1 §3.1).

    Body: {verdict: "resolved"|"watch"|"false_alarm"|null, note?: str, by_name?: str}
    Returns: full Event entity with verdict_* fields populated.

    PATCH with verdict=null clears all four verdict columns (note, by_name,
    timestamp). verdict_at is system-set to now() on every non-null PATCH.
    """
    permission_classes = []  # match project convention; HAProxy guards perimeter

    def patch(self, request, event_id):
        try:
            event = Event.objects.get(event_id=event_id)
        except Event.DoesNotExist:
            raise NotFound(detail="Event not found.")

        ser = EventVerdictSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        verdict_value = ser.validated_data.get("verdict")

        if verdict_value is None:
            # Clear all four columns
            event.verdict = None
            event.verdict_note = None
            event.verdict_by_name = None
            event.verdict_at = None
        else:
            event.verdict = verdict_value
            event.verdict_note = ser.validated_data.get("note") or None
            event.verdict_by_name = ser.validated_data.get("by_name") or None
            event.verdict_at = timezone.now()
        event.save(update_fields=[
            "verdict", "verdict_note", "verdict_by_name", "verdict_at", "updated_at",
        ])
        return Response(EventSerializer(event).data, status=status.HTTP_200_OK)
