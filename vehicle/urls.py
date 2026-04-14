from django.urls import path

from vehicle.views import ListVehicleView, RetrieveVehicleView, UpdateDeleteVehicleView

app_name = "vehicle"

urlpatterns = [
    path("vehicle", ListVehicleView.as_view(), name="vehicle"),
    path(
        "vehicle/by-plate/<str:license_plate>",
        RetrieveVehicleView.as_view(),
    ),
    path(
        "vehicle/<int:id>",
        UpdateDeleteVehicleView.as_view(),
    ),
]
