from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.generics import DestroyAPIView, ListCreateAPIView
from rest_framework.parsers import FormParser, MultiPartParser

from core.pagination import Pagination
from facial.models import Facial
from facial.serializers import FacialSerializer


class ListCreateFacialView(ListCreateAPIView):
    model = Facial
    serializer_class = FacialSerializer
    queryset = Facial.objects.filter(name__isnull=False).exclude(name="")
    parser_classes = (MultiPartParser, FormParser)
    pagination_class = Pagination
    filter_backends = [OrderingFilter, SearchFilter]
    ordering_fields = ["created_at"]
    search_fields = ["name"]


class DeleteFacialView(DestroyAPIView):
    model = Facial
    serializer_class = FacialSerializer
    lookup_field = "id"
    queryset = Facial.objects.all()


# Serve face avatar images through /api/ path (CF tunnel blocks /static/)
import os
from django.conf import settings
from django.http import FileResponse, Http404


def serve_face_avatar(request, filename):
    """Serve face avatar image from upload directory.
    Cloudflare tunnel blocks /static/ paths, so we serve through /api/."""
    # Security: only allow image files, no path traversal
    if '..' in filename or '/' in filename or not filename.endswith(('.jpg', '.jpeg', '.png')):
        raise Http404
    file_path = os.path.join(settings.BASE_DIR_FILE, filename)
    if not os.path.isfile(file_path):
        raise Http404
    return FileResponse(open(file_path, 'rb'), content_type='image/jpeg')
