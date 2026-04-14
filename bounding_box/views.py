from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateDestroyAPIView

from bounding_box.models import BoundingBox
from bounding_box.serializers import BoundingBoxSerializer
from core.pagination import Pagination


class ListCreateBoundingBoxView(ListCreateAPIView):
    model = BoundingBox
    serializer_class = BoundingBoxSerializer
    queryset = BoundingBox.objects.all()
    pagination_class = Pagination
    filter_backends = [OrderingFilter, SearchFilter]
    ordering_fields = ["created_at"]
    search_fields = ["id"]


class UpdateDeleteBoundingBoxView(RetrieveUpdateDestroyAPIView):
    model = BoundingBox
    serializer_class = BoundingBoxSerializer
    lookup_field = "id"
    queryset = BoundingBox.objects.all()
