from django.urls import path
from system.views import SystemTemperatureView, SystemUptimeView

app_name = 'system'

urlpatterns = [
    path('system/temperature', SystemTemperatureView.as_view(), name='temperature'),
    path('system/uptime', SystemUptimeView.as_view(), name='uptime'),
]
