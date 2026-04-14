from django.urls import path

from parcel_detect.views import ParcelDetectView

app_name = "parcel-detect"

urlpatterns = [
    path("parcel-detect", ParcelDetectView.as_view(), name="parcel-detect"),
]
