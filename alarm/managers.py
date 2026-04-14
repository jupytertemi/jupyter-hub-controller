import random
import socket

from django.conf import settings
from django.db import models, transaction
from rest_framework.exceptions import ValidationError

from alarm.enums import AlarmType
from alarm.tasks import alarm_unusual_sound_config, alarm_voice_ai_config
from automation.tasks import create_manual_alarm_automations
from utils.hass_client import HassClient


class AlarmDeviceManager(models.Manager):
    def create(self, **kwargs):
        client = HassClient(
            hass_url=settings.HASS_URL,
            username=settings.HASS_USERNAME,
            password=settings.HASS_PASSWORD,
        )
        client.login()
        
        identity_name = kwargs.get("identity_name")
        entry_id = None

        # FIRST: Check if device already auto-discovered by HA (e.g., via MQTT)
        try:
            entry_id = client.find_esphome_entry_id(identity_name)
            if entry_id:
                print(f"Found existing HA entry for {identity_name}: {entry_id}")
        except Exception as exc:
            print(f"Error checking existing entry: {exc}")

        # SECOND: If not found, try to actively add it via API/mDNS
        if entry_id is None:
            try:
                entry_id = client.add_esphome_device_by_name(identity_name)
                if entry_id:
                    print(f"Added new HA entry for {identity_name}: {entry_id}")
            except socket.gaierror as exc:
                # Device not reachable via hostname - check one more time if it appeared
                entry_id = client.find_esphome_entry_id(identity_name)
                if entry_id is None:
                    raise ValidationError({
                        "error": f"Device {identity_name} not found in Home Assistant. "
                                 "Ensure device is online and visible in HA integrations."
                    }) from exc

        # If still None, fail
        if entry_id is None:
            raise ValidationError({
                "error": f"Device {identity_name} could not be added to Home Assistant. "
                         "Check HA logs and ensure device is discoverable."
            })

        # THIRD: Enable allow_service_calls for the device
        try:
            client.enable_service_calls(entry_id)
            print(f"Enabled service calls for {identity_name}")
        except Exception as exc:
            print(f"Warning: Could not enable service calls: {exc}")

        # Finally: Create Django record and automations
        with transaction.atomic():
            alarm = super().create(hass_entry_id=entry_id, **kwargs)
            create_manual_alarm_automations.delay(entry_id)
            return alarm



class AlarmDeviceConfigManager(models.Manager):
    SOUND_TYPES = ["alarm", "people_home", "running_appliances", "barking_dogs"]
    PERSON_OR_AUDIO = ["PERSON", "AUDIO"]

    def getHassClient(self):
        client = HassClient(
            hass_url=settings.HASS_URL,
            username=settings.HASS_USERNAME,
            password=settings.HASS_PASSWORD,
        )

        client.login()
        return client

    def update_config(self, alarm_config):
        client = self.getHassClient()
        self.set_volume(
            alarm_config.volume, alarm_config.device.identity_name.replace("-", "_")
        )
        automation_id = (
            f"{alarm_config.device.identity_name.replace('-', '_')}_ai_detect"
        )
        client.delete_automation(automation_id)
        self.setup_alarm_automations(alarm_config)

    def create(self, **kwargs):
        alarm_config = super().create(**kwargs)
        self.setup_alarm_automations(alarm_config)
        return alarm_config

    def setup_alarm_automations(self, alarm_config):
        device_name = alarm_config.device.identity_name.replace("-", "_")
        client = self.getHassClient()
        actions = self._create_alarm_say_script(device_name)
        smart_announcements_automation_id = f"{device_name}_smart_announcements"
        if (
            alarm_config.device.type == AlarmType.INDOOR
            and alarm_config.smart_announcement_enabled
        ):
            smart_announcements_triggers = [
                {
                    "trigger": "mqtt",
                    "topic": settings.HASS_MQTT_TOPIC_LISTEN_PARCEL_THEFT_AUTOMATION,
                    "id": "parcel_theft",
                },
                {
                    "trigger": "mqtt",
                    "topic": settings.HASS_MQTT_TOPIC_LISTEN_LOITERING_AUTOMATION,
                    "id": "loitering",
                },
                {
                    "trigger": "mqtt",
                    "topic": settings.HASS_MQTT_TOPIC_LISTEN_VEHICEL_ALARM,
                    "id": "vehicel_alarm",
                },
            ]

            client.create_automation(
                automation_id=smart_announcements_automation_id,
                name=smart_announcements_automation_id,
                triggers=smart_announcements_triggers,
                actions=actions,
                conditions={},
                mode="single",
            )
        else:
            client.delete_automation(smart_announcements_automation_id)
        voice_ai_automation_id = f"{device_name}_voice_ai"
        if alarm_config.voice_ai_enabled is True:
            client.create_automation(
                automation_id=voice_ai_automation_id,
                name=voice_ai_automation_id,
                triggers=[
                    {
                        "trigger": "mqtt",
                        "topic": settings.HASS_MQTT_TOPIC_LISTEN_SPEAK_VOICE_AI,
                        "id": "voice_ai",
                    },
                ],
                actions=actions,
                conditions={},
                mode="single",
            )
        else:
            client.delete_automation(voice_ai_automation_id)

        alarm_voice_ai_config.apply_async(
            args=(alarm_config.voice_ai_enabled, settings.WAKE_WORK_CONTAINER),
            queue="automation_queue",
        )
        alarm_unusual_sound_config.apply_async(
            args=(
                not alarm_config.unusual_sound_enabled,
                settings.SOUND_DETECTION_CONTAINER,
                str(settings.SOUND_DETECTION_PATH),
            ),
            queue="automation_queue",
        )

    def set_volume(self, volume, identity):
        try:
            client = self.getHassClient()
            media_player = f"media_player.{identity}_speaker_media_player"

            resp = client.get_states_entity(media_player)
            if resp.get("state") != "unavailable":
                n = random.randint(1, 100)
                message = {
                    "type": "call_service",
                    "domain": "media_player",
                    "service": "volume_set",
                    "service_data": {
                        "entity_id": media_player,
                        "volume_level": volume / 100,
                    },
                    "id": n,
                }
                client.send_message(message)
            else:
                raise Exception(
                    {"error": "Failed to set volume  device to Home Assistant"}
                )

        except Exception:
            raise Exception({"error": "Failed to set volume device to Home Assistant"})

    # def _get_entity_ids(self, hass_entry_id):
    #     client = self.getHassClient()
    #     speaker_entity = client.get_media_player_entity(hass_entry_id)
    #     return [speaker_entity]

    def _create_alarm_say_script(self, identity):
        media_player = f"media_player.{identity}_speaker_media_player"
        return [
            {
                "variables": {
                    "message": "{{ trigger.payload_json.message | default('hello jupyter') }}"
                }
            },
            {
                "action": "media_player.volume_set",
                "data": {"volume_level": 70 / 100},
                "target": {"entity_id": media_player},
            },
            {
                "action": "tts.speak",
                "metadata": {},
                "data": {
                    "cache": True,
                    "media_player_entity_id": media_player,
                    "message": "{{ message }}",
                },
                "target": {"entity_id": "tts.google_translate_en_com"},
            },
        ]

    def _create_alarm_button_action(self):
        payload_data = (
            "{"
            '"mode": "{{ trigger.payload_json.mode }}", '
            '"device": "{{ trigger.payload_json.device }}",'
            f'"key": "{settings.API_ALARM_MODE_KEY}"'
            "}"
        )

        return [
            {
                "action": "meross_lan.request",
                "metadata": {},
                "data": {
                    "protocol": "http",
                    "method": "PUSH",
                    "namespace": "Appliance.System.All",
                    "payload": payload_data,
                    "host": "http://localhost:8000/api/alarms/mode",
                },
            }
        ]
