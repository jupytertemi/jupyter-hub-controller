from django.db.models.signals import post_delete
from django.dispatch import receiver

from external_device.models import ExternalDevice
from utils.socket_publisher import publish_socket_message


@receiver(post_delete, sender=ExternalDevice)
def external_device_deleted(sender, instance, **kwargs):
    publish_socket_message(
        {
            "action": "remove",
            "type": instance.type,
            "mac": instance.mac_address,
        }
    )
