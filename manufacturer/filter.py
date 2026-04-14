from django_filters import rest_framework as filters

from manufacturer.models import CameraModel


class CameraModelFilter(filters.FilterSet):
    manufacturer_name = filters.CharFilter(
        field_name="camera_manufacturer__manufacturer_name", lookup_expr="icontains"
    )

    class Meta:
        model = CameraModel
        fields = ["manufacturer_name", "camera_manufacturer"]
