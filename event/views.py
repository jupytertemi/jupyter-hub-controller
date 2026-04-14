from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.exceptions import NotFound
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.response import Response

from core.pagination import CustomCursorPagination
from event.filter import EventFilter
from event.models import Event
from event.serializers import EventDetailSerializer, EventSerializer


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
