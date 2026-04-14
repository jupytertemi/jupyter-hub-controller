import json
import logging

from django.conf import settings
from django.db import models
from rest_framework.exceptions import ValidationError

from event.enums import LabelType
from utils.hass_client import HassClient
from vehicle.models import Vehicle


class GarageDoorSettingsManager(models.Manager):

    def create(self, **kwargs):
        self.create_hass_automation(**kwargs)
        settings_data, _ = self.update_or_create(defaults=kwargs)
        return settings_data

    def update_instance(self, instance, **kwargs):
        for key, value in kwargs.items():
            setattr(instance, key, value)
        instance.save()
        self.create_hass_automation(**kwargs)
        return instance

    def delete_instance(self, instance, **kwargs):
        automation_name = f"jupyter_garage_door_{instance.id}"
        instance.delete()
        client = HassClient(
            hass_url=settings.HASS_URL,
            username=settings.HASS_USERNAME,
            password=settings.HASS_PASSWORD,
        )
        client.login()
        self._delete_all_created_automations(client, automation_name)

    def _delete_all_created_automations(self, client, automation_name):
        automation_ids = [
            f"{automation_name}_auto_close",
            f"{automation_name}_auto_open_on_owner",
            f"{automation_name}_trigger_card_on_owner",
            f"{automation_name}_trigger_turn_on",
            f"{automation_name}_trigger_turn_off",
            f"{automation_name}_trigger_card_on_unknown",
        ]

        for automation_id in automation_ids:
            try:
                client.delete_automation(automation_id)
            except Exception as e:
                logging.error(
                    f"[WARN] Failed to delete automation {automation_id}: {e}"
                )

    def create_hass_automation(self, **kwargs):
        client = HassClient(
            hass_url=settings.HASS_URL,
            username=settings.HASS_USERNAME,
            password=settings.HASS_PASSWORD,
        )
        client.login()
        garage_door = kwargs["garage"]
        camera = kwargs["camera"]
        automation_name = f"jupyter_garage_door_{garage_door.id}"

        if not kwargs.get("active_open") or kwargs.get("active_open") is False:
            self._delete_all_created_automations(client, automation_name)
            return
        if not kwargs.get("auto_close"):
            client.delete_automation(f"{automation_name}_auto_close")
        if (
            not kwargs.get("auto_open_on_owner")
            or kwargs.get("auto_open_on_owner") is False
        ):
            client.delete_automation(f"{automation_name}_auto_open_on_owner")
        if not kwargs.get("card_on_owner") or kwargs.get("card_on_owner") is False:
            client.delete_automation(f"{automation_name}_trigger_card_on_owner")
            client.delete_automation(f"{automation_name}_trigger_turn_on")
            client.delete_automation(f"{automation_name}_trigger_turn_off")
        if not kwargs.get("card_on_unknown") or kwargs.get("card_on_unknown") is False:
            client.delete_automation(f"{automation_name}_trigger_card_on_unknown")
        if not camera:
            client.delete_automation(f"{automation_name}_auto_open_on_owner")
            client.delete_automation(f"{automation_name}_trigger_card_on_owner")
            client.delete_automation(f"{automation_name}_trigger_card_on_unknown")
            client.delete_automation(f"{automation_name}_trigger_turn_on")
            client.delete_automation(f"{automation_name}_trigger_turn_off")

        auto_trigger_list = client.automation_trigger(garage_door.hass_entry_id)

        cover_trigger_item = next(
            (
                item
                for item in auto_trigger_list["result"]
                if item.get("domain") == "cover"
            ),
            None,
        )

        if cover_trigger_item is None:
            raise ValidationError(
                {
                    "detail": "Home Assistant cannot connect the device's open and close status,"
                    "so this feature is temporarily unavailable."
                }
            )

        if kwargs.get("auto_close"):
            client.create_automation(
                automation_id=f"{automation_name}_auto_close",
                name=f"{automation_name}_auto_close",
                triggers=self.create_trigger_auto_close(
                    cover_trigger_item, kwargs["auto_close_delay"]
                ),
                actions=self.create_action_cover("close", cover_trigger_item),
                conditions=[],
            )

        if camera:
            if kwargs.get("auto_open_on_owner"):
                self.create_mqtt_automation(
                    automation_id=f"{automation_name}_auto_open_on_owner",
                    name=f"{automation_name}_auto_open_on_owner",
                    topic=settings.HASS_MQTT_TOPIC_LISTEN_EVENT_AUTOMATION,
                    actions=self.create_action_cover("open", cover_trigger_item),
                    conditions=self.create_conditions_card(camera.name),
                )
            if kwargs.get("card_on_owner"):
                self.create_mqtt_automation(
                    automation_id=f"{automation_name}_trigger_card_on_owner",
                    name=f"{automation_name}_trigger_card_on_owner",
                    topic=settings.HASS_MQTT_TOPIC_LISTEN_EVENT_AUTOMATION,
                    actions=self.create_action_card(
                        f"Detected a forgotten car near the {garage_door.name} garage door. "
                        f"Activated an Activity Card to open the garage door.",
                        garage_door.id,
                        "card_on_owner",
                    ),
                    conditions=self.create_conditions_card(camera.name),
                )

            if kwargs.get("card_on_unknown"):
                self.create_mqtt_automation(
                    automation_id=f"{automation_name}_trigger_card_on_unknown",
                    name=f"{automation_name}_trigger_card_on_unknown",
                    topic=settings.HASS_MQTT_TOPIC_LISTEN_EVENT_AUTOMATION,
                    actions=self.create_action_card(
                        f"Detect strange vehicle near the {garage_door.name} garage door."
                        f" Activate Active Tag to alert.",
                        garage_door.id,
                        "card_on_unknown",
                    ),
                    conditions=self.create_conditions_card(camera.name, True),
                )

            if kwargs.get("card_on_owner") or kwargs.get("card_on_unknown"):
                self.create_mqtt_automation(
                    automation_id=f"{automation_name}_trigger_turn_on",
                    name=f"{automation_name}_trigger_turn_on",
                    topic=settings.HASS_MQTT_TOPIC_CONTROL_GARAGE_DEVICE,
                    actions=self.create_action_cover("open", cover_trigger_item),
                    conditions=[
                        {
                            "condition": "template",
                            "value_template": f'{{{{ trigger.payload_json.garage_id == "{garage_door.id}" }}}}',
                        },
                        {
                            "condition": "template",
                            "value_template": '{{ trigger.payload_json.states == "open" }}',
                        },
                    ],
                )

                self.create_mqtt_automation(
                    automation_id=f"{automation_name}_trigger_turn_off",
                    name=f"{automation_name}_trigger_turn_off",
                    topic=settings.HASS_MQTT_TOPIC_CONTROL_GARAGE_DEVICE,
                    actions=self.create_action_cover("close", cover_trigger_item),
                    conditions=[
                        {
                            "condition": "template",
                            "value_template": f'{{{{ trigger.payload_json.garage_id == "{garage_door.id}" }}}}',
                        },
                        {
                            "condition": "template",
                            "value_template": '{{ trigger.payload_json.states == "closing" }}',
                        },
                    ],
                )

    def create_action_cover(self, action_type, cover_trigger_item):
        return [
            {
                "device_id": cover_trigger_item.get("device_id"),
                "domain": "cover",
                "entity_id": cover_trigger_item.get("entity_id"),
                "type": action_type,
            }
        ]

    def create_trigger_auto_close(self, cover_trigger_item, auto_close_delay):
        return [
            {
                "device_id": cover_trigger_item.get("device_id"),
                "domain": "cover",
                "entity_id": cover_trigger_item.get("entity_id"),
                "type": "opened",
                "trigger": "device",
                "for": {"hours": 0, "minutes": int(auto_close_delay), "seconds": 0},
            }
        ]

    def create_conditions_card(
        self,
        camera_name,
        action_type=False,
    ):
        in_operator = "not in" if action_type else "in"
        vehicle_plates = list(Vehicle.objects.values_list("license_plate", flat=True))
        return [
            {
                "condition": "template",
                "value_template": f'{{{{ trigger.payload_json.label == "{LabelType.CAR}" }}}}',
            },
            {
                "condition": "template",
                "value_template": f'{{{{ trigger.payload_json.camera_name == "{camera_name}" }}}}',
            },
            {
                "condition": "template",
                "value_template": f"{{{{ trigger.payload_json.vehicle_plate {in_operator} {vehicle_plates} }}}}",
            },
            {
                "condition": "template",
                "value_template": "{{ trigger.payload_json.vehicle_status in ['Approaching', 'Departing'] }}",
            },
        ]

    def create_action_card(self, message, garage_id, type):
        return [
            {
                "action": "mqtt.publish",
                "data": {
                    "evaluate_payload": False,
                    "qos": "0",
                    "retain": False,
                    "topic": settings.HASS_MQTT_TOPIC_PUBLISH_CARD_GARAGE,
                    "payload": json.dumps(
                        {
                            "message": message,
                            "garage_id": str(garage_id),
                            "type": type,
                            "audio_path": "{{trigger.payload_json.audio_path}}",
                            "vehicle_plate": "{{trigger.payload_json.vehicle_plate}}",
                            "event_id": "{{trigger.payload_json.event_id}}",
                        }
                    ),
                },
            }
        ]

    def create_mqtt_automation(
        self, automation_id, name, topic, actions, conditions=None
    ):
        client = HassClient(
            hass_url=settings.HASS_URL,
            username=settings.HASS_USERNAME,
            password=settings.HASS_PASSWORD,
        )
        client.login()
        client.create_automation(
            automation_id=automation_id,
            name=name,
            triggers=[
                {
                    "trigger": "mqtt",
                    "topic": topic,
                }
            ],
            actions=actions,
            conditions=conditions or [],
        )
