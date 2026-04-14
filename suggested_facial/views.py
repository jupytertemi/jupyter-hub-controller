from django.db import transaction
from rest_framework import status
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.generics import ListAPIView, UpdateAPIView
from rest_framework.response import Response

from core.pagination import Pagination
from suggested_facial.models import SuggestedFacial
from suggested_facial.serializers import SuggestedFacialSerializer
from suggested_facial.services import ignore_person, update_person


class ListSuggestedFacialView(ListAPIView):
    model = SuggestedFacial
    serializer_class = SuggestedFacialSerializer
    queryset = SuggestedFacial.objects.filter(is_ignore=False)
    pagination_class = Pagination
    filter_backends = [OrderingFilter, SearchFilter]
    ordering_fields = ["created_at"]
    search_fields = ["suggested_name"]


class UpdateSuggestedFacialView(UpdateAPIView):
    queryset = SuggestedFacial.objects.all()
    serializer_class = SuggestedFacialSerializer

    def update(self, request, *args, **kwargs):
        """Custom update logic"""
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        try:
            with transaction.atomic():
                if request.data.get("is_ignore"):
                    ignore_person(
                        person_id=instance.person_id, is_almost=instance.is_almost
                    )

                if request.data.get("suggested_name"):
                    update_person(
                        person_id=instance.person_id,
                        is_almost=instance.is_almost,
                        sugested_name=request.data.get("suggested_name"),
                    )

                serializer = self.get_serializer(
                    instance, data=request.data, partial=partial
                )
                serializer.is_valid(raise_exception=True)
                self.perform_update(serializer)
                return Response(serializer.data, status=status.HTTP_200_OK)
        except Exception as err:
            raise ValueError(err)
