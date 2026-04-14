from django.urls import path

from cloudflare_turn.views import ListCreateTurnView

app_name = "cloudflare_turn"

urlpatterns = [
    path("turn/revoke", ListCreateTurnView.as_view(), name="cloudflare_turn"),
]
