from django.urls import path
from system.views import SystemTemperatureView

app_name = 'system'

urlpatterns = [
    path('system/temperature', SystemTemperatureView.as_view(), name='temperature'),
]
