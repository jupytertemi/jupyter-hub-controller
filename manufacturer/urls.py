from django.urls import path

from manufacturer.views import ListCameraManufacturerView, ListCameraModelView

app_name = "manufacturer"

urlpatterns = [
    path(
        "manufacturer",
        ListCameraManufacturerView.as_view(),
        name="manufacturer",
    ),
    path("manufacturer/model", ListCameraModelView.as_view(), name="cameras/model"),
]
