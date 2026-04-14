from django.apps import AppConfig


class ExternalDeviceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "external_device"

    def ready(self):
        import external_device.signals  # noqa
