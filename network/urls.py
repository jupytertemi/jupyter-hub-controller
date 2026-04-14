from django.urls import path

from network.views import GetWifiCredentialsView, WifiScanView, WifiConnectView, WifiStatusView

app_name = "network"

urlpatterns = [
    path("network/wifi-credentials", GetWifiCredentialsView.as_view(), name="network"),
    path("network/wifi-scan", WifiScanView.as_view(), name="wifi_scan"),
    path("network/wifi-status", WifiStatusView.as_view(), name="wifi_status"),
    path("network/wifi-connect", WifiConnectView.as_view(), name="wifi_connect"),
]
