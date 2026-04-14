import json
import logging
import time

from django.conf import settings
from django.db import models

from alarm.enums import AlarmMode, OccupancyIllusion
from alarm.models import AlarmDeviceConfig
from automation.enums import (
    AlarmScheduleRepeatType,
    AlarmSettingsMode,
    AlarmSound,
    AlarmTriggerConditions,
)
from automation.tasks import automation_alarm_loitering_config
from external_device.enum import ExternalType
from external_device.models import ExternalDevice
from utils.hass_client import HassClient
from utils.mqtt_client import MQTTClient

SOUND_TYPES = [
    "alarm",
    "people_home",
    "running_appliances",
    "barking_dogs",
]

PERSON_OR_AUDIO = ["PERSON", "AUDIO"]


class AutomationBuilder:
    def __init__(self, alias, mode="restart"):
        self.automation = {
            "alias": alias,
            "triggers": [],
            "conditions": [],
            "actions": [],
            "mode": mode,
        }

    def add_trigger(self, trigger):
        self.automation["triggers"].append(trigger)
        return self

    def add_mqtt_trigger(self, topic, trigger_id=None):
        trigger = {
            "trigger": "mqtt",
            "topic": topic,
        }
        if trigger_id is not None:
            trigger["id"] = trigger_id
        return self.add_trigger(trigger)

    def add_triggers(self, triggers):
        self.automation["triggers"].extend(triggers)
        return self

    def add_condition(self, condition):
        self.automation["conditions"].append(condition)
        return self

    def add_template_condition(self, value_template):
        return self.add_condition(
            {
                "condition": "template",
                "value_template": value_template,
            }
        )

    def add_conditions(self, conditions):
        self.automation["conditions"].extend(conditions)
        return self

    def add_action(self, action):
        self.automation["actions"].append(action)
        return self

    def add_mqtt_publish_action(
        self,
        topic,
        payload,
        evaluate_payload=False,
        qos="0",
        retain=False,
    ):
        return self.add_action(
            {
                "action": "mqtt.publish",
                "data": {
                    "evaluate_payload": evaluate_payload,
                    "qos": qos,
                    "retain": retain,
                    "topic": topic,
                    "payload": payload,
                },
            }
        )

    def add_actions(self, actions):
        self.automation["actions"].extend(actions)
        return self

    def set_mode(self, mode):
        self.automation["mode"] = mode
        return self

    def build(self):
        return self.automation


class AutomationScriptBuilder:
    def __init__(self, alias, mode="restart"):
        self.script = {
            "alias": alias,
            "sequence": [],
            "mode": mode,
        }

    def add_step(self, step):
        self.script["sequence"].append(step)
        return self

    def add_steps(self, steps):
        self.script["sequence"].extend(steps)
        return self

    def set_mode(self, mode):
        self.script["mode"] = mode
        return self

    def build(self):
        return self.script


class AlarmSettingsManager(models.Manager):

    # =========================
    # CRUD
    # =========================

    def create(self, **kwargs):
        entry_sensor_ids = kwargs.pop("entry_sensor_ids", None)
        settings_data = super().create(**kwargs)
        self._sync_entry_sensors(settings_data, entry_sensor_ids)
        self.setup_alarm_automations(settings_data)
        return settings_data

    def update_instance(self, instance, **kwargs):
        entry_sensor_ids = kwargs.pop("entry_sensor_ids", None)
        requested_mode = kwargs.get("mode", instance.mode)
        if requested_mode == AlarmSettingsMode.NONE.value:
            kwargs["entry_door_activate"] = False
        else:
            kwargs.setdefault("entry_door_activate", True)
            kwargs.setdefault("entry_door_all_sensors", True)

        old_mode = instance.mode
        for key, value in kwargs.items():
            setattr(instance, key, value)
        SOUND_TO_OCCUPANCY_MAP = {
            AlarmSound.PEOPLE_HOME.value: OccupancyIllusion.PEOPLE.value,
            AlarmSound.RUNNING_APPLIANCES.value: OccupancyIllusion.RUNNING_APPLIANCES.value,
            AlarmSound.BARKING_DOGS.value: OccupancyIllusion.DOGS.value,
        }
        instance.save()
        new_mode = instance.mode
        if instance.mode == AlarmSettingsMode.NONE.value:
            payload = {
                "alarm_mode": AlarmMode.OFF.value,
                "occupancy_illusion": OccupancyIllusion.OFF.value,
            }

        elif instance.mode == AlarmSettingsMode.TRAVEL.value:
            occupancy = SOUND_TO_OCCUPANCY_MAP.get(instance.sound)

            payload = {
                "alarm_mode": AlarmMode.OFF.value,
                "occupancy_illusion": occupancy or OccupancyIllusion.OFF.value,
                "smart_announcement_enabled": False,
            }

        else:
            payload = {
                "alarm_mode": instance.mode,
                "occupancy_illusion": OccupancyIllusion.OFF.value,
                "smart_announcement_enabled": False,
            }

        AlarmDeviceConfig.objects.filter(device=instance.device).update(**payload)
        self._sync_entry_sensors(instance, entry_sensor_ids)
        self.setup_alarm_automations(instance)
        if old_mode != new_mode:
            self.publish_alarm_mode(instance.device.identity_name, new_mode)
        return instance

    def _sync_entry_sensors(self, settings_data, entry_sensor_ids=None):
        if not settings_data.entry_door_activate:
            settings_data.entry_sensors.clear()
            return

        if settings_data.entry_door_all_sensors:
            sensors = ExternalDevice.objects.filter(type=ExternalType.S1)
            settings_data.entry_sensors.set(sensors)
            return

        if entry_sensor_ids is None:
            return

        sensors = ExternalDevice.objects.filter(
            id__in=entry_sensor_ids,
            type=ExternalType.S1,
        )
        settings_data.entry_sensors.set(sensors)

    # =========================
    # MAIN SETUP
    # =========================

    def setup_alarm_automations(self, settings_data):
        identity = settings_data.device.identity_name.replace("-", "_")

        client = HassClient(
            hass_url=settings.HASS_URL,
            username=settings.HASS_USERNAME,
            password=settings.HASS_PASSWORD,
        )
        client.login()

        self._delete_old_automations(client, identity)

        self._dispatch_loitering_config(settings_data.loitering_activate)

        # Manual alarm
        self._create_automation_manual_alarm(
            client,
            f"{identity}_manual_trigger",
            settings_data,
            identity,
        )

        # Stop all
        stop_all_id = f"{identity}_alarm_stop_all"
        stop_all_builder = (
            AutomationBuilder(stop_all_id, mode="single")
            .add_mqtt_trigger(settings.HASS_MQTT_TOPIC_LISTEN_EVENT_TURN_OFF_AUTOMATION)
            .add_mqtt_trigger(f"/{identity}_turn_off")
            .add_mqtt_trigger(
                f"/{settings_data.device.identity_name}/keyfob_status",
                trigger_id="keyfob_status",
            )
            .add_conditions(self._create_keyfob_turn_off_conditions())
            .add_actions(self._create_action_turn_off(identity))
        )
        self._create_hass_automation(client, stop_all_id, stop_all_builder)

        if settings_data.mode == "none":
            return

        topic_listen = self._get_topic_listen(settings_data)
        if topic_listen:
            self._create_topic_automations(
                client,
                topic_listen,
                settings_data,
                identity,
            )

        if settings_data.known_face_disarm:
            self._create_alarm_disable_on_face_recognition(
                client,
                settings_data,
                identity,
            )

    # =========================
    # AI / MQTT AUTOMATIONS
    # =========================

    def _create_topic_automations(
        self,
        client,
        topic_listen,
        settings_data,
        identity,
    ):
        script_name = f"{identity}_ai_detected"

        self._create_hass_script(
            client=client,
            script_id=script_name,
            builder=AutomationScriptBuilder(script_name, mode="single").add_steps(
                self._create_alarm_media_play_script(
                    identity,
                    settings_data.sound_duration,
                    settings_data.volume,
                    manual=False,
                    sound=(
                        settings_data.sound
                        if settings_data.mode == AlarmSettingsMode.TRAVEL.value
                        else AlarmSound.ALARM.value
                    ),
                )
            ),
        )

        triggers = [
            {
                "trigger": "mqtt",
                "topic": topic,
                "id": event_type,
            }
            for event_type, topic in topic_listen.items()
        ]

        if settings_data.live_activity_prompt:
            actions = [
                {
                    "action": "mqtt.publish",
                    "data": {
                        "evaluate_payload": False,
                        "qos": "0",
                        "retain": False,
                        "topic": settings.HASS_MQTT_TOPIC_PUBLISH_ALARM,
                        "payload": json.dumps(
                            {
                                "message": "Prompt user with bottom card to activate",
                                "audio_path": "{{ trigger.payload_json.audio_path }}",
                                "event_id": "{{ trigger.payload_json.event_id }}",
                                "mode": settings_data.mode,
                                "card_type": "{{ trigger.id }}",
                            }
                        ),
                    },
                }
            ]
        else:
            actions = self._create_action(
                script_name,
                identity,
                settings_data.schedule,
                settings_data.repeat_type,
                True,
            )

        if settings_data.entry_door_activate:
            actions = self._create_entry_door_event_actions(settings_data) + actions

        entry_sensor_macs = self._get_entry_sensor_macs(settings_data)
        entry_door_armed_after_ts = self._get_entry_door_armed_after_ts(settings_data)

        topic_builder = (
            AutomationBuilder(script_name, mode="single")
            .add_triggers(triggers)
            .add_actions(actions)
            .add_conditions(
                self._create_conditions(
                    settings_data.schedule,
                    settings_data.live_activity_prompt,
                    settings_data.known_face_disarm,
                    settings_data.repeat_type,
                    settings_data.schedule_repeat,
                    settings_data.schedule_start,
                    settings_data.schedule_end,
                    entry_sensor_macs=entry_sensor_macs,
                    entry_door_armed_after_ts=entry_door_armed_after_ts,
                )
            )
        )
        self._create_hass_automation(client, script_name, topic_builder)

    # =========================
    # FACE DISARM
    # =========================

    def _create_alarm_disable_on_face_recognition(
        self,
        client,
        settings_data,
        identity,
    ):
        known_face_id = f"{identity}_known_face_disarm"
        known_face_builder = (
            AutomationBuilder(known_face_id, mode="single")
            .add_mqtt_trigger(settings.HASS_MQTT_TOPIC_LISTEN_EVENT_AUTOMATION)
            .add_mqtt_publish_action(
                topic=f"/{identity}_turn_off",
                payload=json.dumps({"label": "PERSON"}),
            )
            .add_conditions(
                self._create_conditions_turn_off(
                    settings_data.schedule,
                    settings_data.repeat_type,
                    settings_data.schedule_repeat,
                    settings_data.schedule_start,
                    settings_data.schedule_end,
                )
            )
        )
        self._create_hass_automation(client, known_face_id, known_face_builder)

    # =========================
    # ACTIONS / CONDITIONS
    # =========================

    def _create_action(
        self,
        automation_name,
        identity,
        schedule,
        repeat_type,
        live_activity_prompt=False,
        delay=None,
    ):
        actions = []

        if delay:
            actions.append(
                {
                    "delay": {
                        "minutes": int(delay),
                    }
                }
            )

        actions.extend(
            [
                {
                    "action": "mqtt.publish",
                    "data": {
                        "topic": f"/{identity}_turn_off",
                        "payload": json.dumps({"label": "PERSON", "script": True}),
                    },
                },
                {"delay": {"milliseconds": 500}},
            ]
        )

        if live_activity_prompt:
            actions.extend([{"action": f"script.{automation_name}"}])
        else:
            actions.append(
                {
                    "if": [
                        {
                            "condition": "template",
                            "value_template": (
                                "{{ trigger.payload_json is defined "
                                "and trigger.payload_json.sound is not none }}"
                            ),
                        }
                    ],
                    "then": [
                        {
                            "action": f"script.{automation_name}",
                            "data": {"sound": "{{ trigger.payload_json.sound }}"},
                        }
                    ],
                    "else": [{"action": f"script.{automation_name}"}],
                }
            )

        if schedule and repeat_type == AlarmScheduleRepeatType.NEVER:
            actions.append(
                {
                    "action": "automation.turn_off",
                    "target": {"entity_id": f"automation.{automation_name}"},
                }
            )

        return [{"sequence": actions}]

    def _create_conditions(
        self,
        schedule,
        manual_prompt,
        trusted,
        repeat_type,
        days,
        start,
        end,
        entry_sensor_macs=None,
        entry_door_armed_after_ts=None,
    ):
        conditions = [
            {
                "condition": "template",
                "value_template": (
                    f"{{{{ trigger.payload_json.label in {PERSON_OR_AUDIO} }}}}"
                ),
            }
        ]

        if entry_sensor_macs is not None:
            conditions.append(
                {
                    "condition": "template",
                    "value_template": (
                        "{{ trigger.id != 'entry_door_opened' or "
                        "trigger.payload_json.mac in %s }}" % entry_sensor_macs
                    ),
                }
            )

        conditions.append(
            {
                "condition": "template",
                "value_template": (
                    "{{ trigger.id != 'entry_door_opened' or "
                    "trigger.payload_json.action == 'opened' }}"
                ),
            }
        )

        if entry_door_armed_after_ts is not None:
            conditions.append(
                {
                    "condition": "template",
                    "value_template": (
                        "{{ trigger.id != 'entry_door_opened' or "
                        "as_timestamp(now()) >= %d }}" % int(entry_door_armed_after_ts)
                    ),
                }
            )

        if trusted and not manual_prompt:
            conditions.append(
                {
                    "condition": "template",
                    "value_template": AlarmTriggerConditions.TRUSTED_FACE_DISARM,
                }
            )

        if schedule:
            if repeat_type == AlarmScheduleRepeatType.CUSTOM:
                conditions.append(
                    {
                        "condition": "template",
                        "value_template": (f"{{{{ now().strftime('%A') in {days} }}}}"),
                    }
                )

            if start:
                conditions.append(
                    {
                        "condition": "template",
                        "value_template": (f"{{{{ as_timestamp(now()) >= {start} }}}}"),
                    }
                )

            if end:
                conditions.append(
                    {
                        "condition": "template",
                        "value_template": (f"{{{{ as_timestamp(now()) <= {end} }}}}"),
                    }
                )

        return conditions

    def _create_conditions_turn_off(
        self,
        schedule,
        repeat_type,
        days,
        start,
        end,
    ):
        return [
            {
                "condition": "template",
                "value_template": AlarmTriggerConditions.NOT_TRUSTED_FACE_DISARM,
            },
            {
                "condition": "template",
                "value_template": (
                    AlarmTriggerConditions.FACE_CONFIDENCE_ABOVE_THRESHOLD
                ),
            },
            *self._create_conditions(
                schedule,
                False,
                False,
                repeat_type,
                days,
                start,
                end,
            ),
        ]

    def _create_keyfob_turn_off_conditions(self):
        return [
            {
                "condition": "template",
                "value_template": (
                    "{{ trigger.id != 'keyfob_status' or "
                    "trigger.payload_json.action == 'activated' }}"
                ),
            }
        ]

    def _create_action_turn_off(self, identity):
        device_name = identity.replace("_", "-")
        return [
            {
                "action": "mqtt.publish",
                "data": {
                    "topic": f"/{device_name}/mode",
                    "payload": json.dumps(
                        {"device_name": device_name, "mode": "disarm"}
                    ),
                },
            },
            {
                "action": "script.turn_off",
                "target": {
                    "entity_id": [
                        f"script.{identity}_ai_detected",
                        f"script.{identity}_manual_trigger",
                    ]
                },
            },
        ]

    # =========================
    # MEDIA SCRIPT
    # =========================

    def _create_alarm_media_play_script(
        self,
        identity,
        duration,
        volume,
        manual=False,
        sound="alarm",
    ):
        device_name = identity.replace("_", "-")
        total_seconds = max(int(duration), 1) * 60
        start_payload = (
            '{"device_name": "%s", "mode": "{{ sound }}"}' % device_name
            if manual
            else json.dumps({"device_name": device_name, "mode": sound})
        )
        payload_data = (
            '{"time_left_seconds": {{ [total + 2 - repeat.index * 2, 0] | max }}, '
            '"sound": "{{ sound }}"}'
            if manual
            else (
                "{"
                '"time_left_seconds": {{ [total + 2 - repeat.index * 2, 0] | max }}, '
                f'"sound": "{sound}"'
                "}"
            )
        )

        return [
            {"variables": {"total": total_seconds}},
            {
                "parallel": [
                    {
                        "sequence": [
                            {
                                "action": "mqtt.publish",
                                "data": {
                                    "topic": f"/{device_name}/mode",
                                    "payload": start_payload,
                                },
                            },
                            {"delay": {"seconds": total_seconds}},
                        ]
                    },
                    {
                        "repeat": {
                            "count": "{{ (total / 2) | int }}",
                            "sequence": [
                                {
                                    "action": "mqtt.publish",
                                    "data": {
                                        "topic": f"/{identity}_countdown",
                                        "payload": payload_data,
                                    },
                                },
                                {"delay": {"seconds": 2}},
                            ],
                        }
                    },
                ]
            },
            {
                "action": "mqtt.publish",
                "data": {
                    "topic": f"/{device_name}/mode",
                    "payload": json.dumps(
                        {"device_name": device_name, "mode": sound}
                    ),
                },
            },
        ]

    # =========================
    # CLEANUP
    # =========================

    def _delete_old_automations(self, client, identity):
        for automation_id in [
            f"{identity}_ai_detected",
            f"{identity}_known_face_disarm",
        ]:
            try:
                client.delete_automation(automation_id)
            except Exception as exc:
                logging.error(f"Failed to delete automation {automation_id}: {exc}")

        for script_id in [f"{identity}_ai_detected"]:
            try:
                client.delete_script(script_id)
            except Exception as exc:
                logging.error(f"Failed to delete script {script_id}: {exc}")

    # =========================
    # HELPERS
    # =========================

    def _dispatch_loitering_config(self, enabled: bool):
        automation_alarm_loitering_config.apply_async(
            args=(
                enabled,
                settings.PARCEL_CONTAINER_NAME,
                str(settings.PARCEL_CONFIG_PATH),
            ),
            queue="automation_queue",
        )

    def _get_topic_listen(self, settings_data):
        topics = {
            "unusual_sound_detected": (
                settings.HASS_MQTT_TOPIC_LISTEN_UNSUAL_SOUND_AUTOMATION
            ),
            "parcel_theft_detected": (
                settings.HASS_MQTT_TOPIC_LISTEN_PARCEL_THEFT_AUTOMATION
            ),
            "loitering_detected": (
                settings.HASS_MQTT_TOPIC_LISTEN_LOITERING_AUTOMATION
            ),
        }

        if settings_data.entry_door_activate:
            topics["entry_door_opened"] = (
                f"/{settings_data.device.identity_name}/entrydoor_status"
            )

        return {
            event_type: topic
            for event_type, topic in topics.items()
            if event_type == "entry_door_opened"
            or getattr(
                settings_data,
                f"{event_type.replace('_detected', '')}_activate",
                False,
            )
        }

    def _get_entry_sensor_macs(self, settings_data):
        if not settings_data.entry_door_activate:
            return None

        if settings_data.entry_door_all_sensors:
            return list(
                ExternalDevice.objects.filter(type=ExternalType.S1).values_list(
                    "mac_address", flat=True
                )
            )

        return list(
            settings_data.entry_sensors.filter(type=ExternalType.S1).values_list(
                "mac_address", flat=True
            )
        )

    def _get_entry_sensor_queryset(self, settings_data):
        if settings_data.entry_door_all_sensors:
            return ExternalDevice.objects.filter(type=ExternalType.S1)
        return settings_data.entry_sensors.filter(type=ExternalType.S1)

    def _get_entry_sensor_name_template(self, settings_data):
        sensors = list(
            self._get_entry_sensor_queryset(settings_data).values("mac_address", "name")
        )
        if not sensors:
            return "Entry Sensor"

        mapping = {sensor["mac_address"]: sensor["name"] for sensor in sensors}
        return "{{ %s.get(trigger.payload_json.mac, 'Entry Sensor') }}" % mapping

    def _create_entry_door_event_actions(self, settings_data):
        event_payload = json.dumps(
            {
                "title": f"{settings_data.device.name} is active.",
                "message": f"{self._get_entry_sensor_name_template(settings_data)} is opened.",
                "event_id": None,
                "label": "ENTRYDOOR_OPEND_ALARM_ACTIVE",
                "created_at": "{{ utcnow().isoformat() }}",
            }
        )
        return [
            {
                "if": [
                    {
                        "condition": "template",
                        "value_template": (
                            "{{ trigger.id == 'entry_door_opened' and "
                            "trigger.payload_json.action == 'opened' }}"
                        ),
                    }
                ],
                "then": [
                    {
                        "action": "mqtt.publish",
                        "data": {
                            "topic": settings.HASS_MQTT_TOPIC_EVENT_ALARM_ACTIVE,
                            "payload": event_payload,
                        },
                    }
                ],
            }
        ]

    def _get_entry_door_armed_after_ts(self, settings_data):
        if (
            not settings_data.entry_door_activate
            or settings_data.entry_door_exit_delay_seconds <= 0
        ):
            return None
        return int(settings_data.updated_at.timestamp()) + int(
            settings_data.entry_door_exit_delay_seconds
        )

    def _create_hass_automation(
        self, client, automation_id, builder: AutomationBuilder
    ):
        automation = builder.build()
        client.create_automation(
            automation_id=automation_id,
            name=automation["alias"],
            triggers=automation["triggers"],
            actions=automation["actions"],
            conditions=automation["conditions"],
            mode=automation["mode"],
        )

    def _create_hass_script(
        self, client, script_id, builder: AutomationScriptBuilder
    ):
        script = builder.build()
        client.create_automation_script(
            automation_id=script_id,
            name=script["alias"],
            sequence=script["sequence"],
            mode=script["mode"],
        )

    def _create_automation_manual_alarm(
        self,
        client,
        name,
        settings_data,
        identity,
    ):
        self._create_hass_script(
            client=client,
            script_id=name,
            builder=AutomationScriptBuilder(name).add_steps(
                self._create_alarm_media_play_script(
                    identity,
                    1,
                    settings_data.volume,
                    manual=True,
                )
            ),
        )

        actions = self._create_action(
            name,
            identity,
            False,
            False,
            False,
        )

        manual_builder = (
            AutomationBuilder(name)
            .add_mqtt_trigger(settings.HASS_MQTT_TOPIC_CONTROL_MANUAL_ALARM_DEVICE)
            .add_mqtt_trigger(f"/{identity}_turn_on")
            .add_actions(actions)
            .add_template_condition(
                f"{{{{ trigger.payload_json.sound in {SOUND_TYPES} }}}}"
            )
        )
        self._create_hass_automation(client, name, manual_builder)

    def publish_alarm_mode(self, identity, mode):
        topic = f"/{identity}/mode"
        try:
            mqtt_client = MQTTClient(
                host=settings.MQTT_HOST,
                port=settings.MQTT_PORT,
                username=settings.MQTT_USERNAME,
                password=settings.MQTT_PASSWORD,
            )
            mqtt_client.connect()

            mqtt_client.publish(
                topic,
                json.dumps({"device_name": identity, "mode": mode}),
            )
            print('123')
            time.sleep(0.5)
            mqtt_client.publish(
                topic,
                json.dumps({"device_name": identity, "mode": mode}),
            ) 
            print('456')
            mqtt_client.close()
        except Exception as e:
            logging.exception("MQTT connect/publish failed")
            logging.error(
                {
                    "status": "error",
                    "message": "Failed to send alarm command",
                    "detail": str(e),
                }
            )
