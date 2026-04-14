import os, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hub_controller.settings.production")
django.setup()
from django.contrib.auth.models import User
hub_secret = os.environ.get("HUB_SECRET", "")
if not hub_secret:
    print("HUB_SECRET not set, skipping hub user creation")
elif not User.objects.filter(username="hub").exists():
    User.objects.create_user(username="hub", password=hub_secret, is_active=True)
    print("Created hub user for API authentication")
else:
    user = User.objects.get(username="hub")
    user.set_password(hub_secret)
    user.save()
    print("Updated hub user password")
