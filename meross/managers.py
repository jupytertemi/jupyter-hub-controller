import requests
from django.conf import settings
from django.db import models
from rest_framework.exceptions import ValidationError

from utils.hass_client import HassClient, HomeAssistantUnavailable


class MerossDeviceManager(models.Manager):
    def _is_flow_expired_error(self, error):
        detail = getattr(error, "detail", None)
        if not isinstance(detail, dict):
            return False
        reason = detail.get("error")
        if isinstance(reason, (list, tuple)):
            reason = reason[0] if reason else ""
        return str(reason) == "flow_expired"

    def _entry_name(self, entry, fallback_name):
        return (
            entry.get("title")
            or fallback_name
            or f"Meross-{entry.get('entry_id', '')[:6]}"
        )

    def _create_from_new_meross_entry(self, client, name, before_entry_ids=None):
        current_entry_ids = set(
            self.get_queryset().values_list("hass_entry_id", flat=True)
        )
        entries = client.get_meross_config_entries()

        candidates = [entry for entry in entries if entry.get("entry_id")]
        if before_entry_ids is not None:
            candidates = [
                entry
                for entry in candidates
                if entry.get("entry_id") not in before_entry_ids
            ]

        candidates = [
            entry
            for entry in candidates
            if entry.get("entry_id") not in current_entry_ids
        ]

        if len(candidates) == 1:
            entry = candidates[0]
            return super().create(
                hass_entry_id=entry.get("entry_id"),
                name=self._entry_name(entry, name),
            )

        # If HA already has entries not saved in local DB, sync them instead of failing hard.
        synced = []
        for entry in entries:
            entry_id = entry.get("entry_id")
            if not entry_id or entry_id in current_entry_ids:
                continue
            synced.append(
                super().create(
                    hass_entry_id=entry_id,
                    name=self._entry_name(entry, name),
                )
            )
            current_entry_ids.add(entry_id)

        if synced:
            return synced[0]

        raise ValidationError(
            {
                "error": "flow_expired",
                "message": (
                    "Meross flow expired. Device may already be added in Home Assistant. "
                    "Refresh discovery and retry."
                ),
            }
        )

    def create(self, **kwargs):
        client = HassClient(
            hass_url=settings.HASS_URL,
            username=settings.HASS_USERNAME,
            password=settings.HASS_PASSWORD,
        )
        try:
            client.login()
        except requests.exceptions.RequestException as exc:
            raise HomeAssistantUnavailable(
                {
                    "error": "Failed to connect to Home Assistant",
                    "details": str(exc),
                }
            ) from exc
        before_entry_ids = set()
        try:
            before_entry_ids = {
                entry.get("entry_id")
                for entry in client.get_meross_config_entries()
                if entry.get("entry_id")
            }
        except Exception:
            # Continue; fallback still works without baseline snapshot.
            before_entry_ids = set()
        try:
            entry = client.add_meross_device(kwargs.get("flow_id"))
        except ValidationError as err:
            if self._is_flow_expired_error(err):
                try:
                    return self._create_from_new_meross_entry(
                        client, kwargs.get("name"), before_entry_ids
                    )
                except ValidationError:
                    raise
                except Exception:
                    raise err
            raise
        except Exception as err:
            raise ValidationError({"error": err})

        hass_entry_id = entry.get("result").get("entry_id")
        meross = super().create(hass_entry_id=hass_entry_id, name=kwargs.get("name"))
        return meross


class MerossCloudAccountManager(models.Manager):
    def create(self, **kwargs):
        client = HassClient(
            hass_url=settings.HASS_URL,
            username=settings.HASS_USERNAME,
            password=settings.HASS_PASSWORD,
        )
        try:
            client.login()
        except requests.exceptions.RequestException as exc:
            raise HomeAssistantUnavailable(
                {
                    "error": "Failed to connect to Home Assistant",
                    "details": str(exc),
                }
            ) from exc
        try:
            entry = client.add_meross_cloud(**kwargs)
        except ValidationError:
            raise
        except Exception as err:
            raise ValidationError({"error": err})
        hass_entry_id = entry.get("result").get("entry_id")
        validated_data = kwargs
        validated_data.setdefault("hass_entry_id", hass_entry_id)
        validated_data.pop("password")
        validated_data.pop("save_password")
        validated_data.pop("allow_mqtt_publish")
        validated_data.pop("check_firmware_updates")
        alarm = super().create(**validated_data)
        return alarm
