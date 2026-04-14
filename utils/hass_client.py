import logging
import socket
import time

import requests
from django.conf import settings
from rest_framework import serializers
from rest_framework.exceptions import APIException
from rest_framework.generics import GenericAPIView

from utils.websocket_client import WebSocketClient


class HassClient:
    def __init__(self, hass_url, username, password):
        self._hass_url = hass_url
        self._username = username
        self._password = password
        self._token = None

    def login(self):
        """
        Logs in to the Home Assistant instance and retrieves an access token.

        This method performs the following steps:
        1. Initiates a login flow to get a flow ID.
        2. Submits the username and password to the login flow to get an authorization code.
        3. Exchanges the authorization code for an access token.

        Raises:
            requests.exceptions.HTTPError: If an HTTP error occurs during any of the requests.

        Returns:
            None
        """

        url = f"{self._hass_url}/auth/login_flow"
        data = {
            "client_id": self._hass_url,
            "handler": ["homeassistant", None],
            "redirect_uri": f"{self._hass_url}/?auth_callback=1",
        }
        response = requests.post(url, json=data, timeout=10)
        response.raise_for_status()
        flow_id = response.json().get("flow_id")

        url = f"{self._hass_url}/auth/login_flow/{flow_id}"
        data = {
            "client_id": self._hass_url,
            "username": self._username,
            "password": self._password,
        }
        response = requests.post(url, json=data, timeout=10)
        response.raise_for_status()
        code = response.json().get("result")
        url = f"{self._hass_url}/auth/token"
        data = {
            "client_id": self._hass_url,
            "grant_type": "authorization_code",
            "code": code,
        }
        response = requests.post(url, data=data, timeout=10)
        response.raise_for_status()

        self._token = response.json().get("access_token")

    def add_esphome_device(self, host, port):
        """
        Adds an ESPHome device to the Home Assistant instance.
        This method initiates a configuration flow for an ESPHome device and completes
        the flow by providing the device's host and port.
        Args:
            host (str): The hostname or IP address of the ESPHome device.
            port (int): The port number on which the ESPHome device is accessible.
        Returns:
            str: The entry ID of the newly added ESPHome device.
        Raises:
            requests.exceptions.HTTPError: If the HTTP request to Home Assistant fails.
        """

        retries = 6
        for attempt in range(retries):
            try:
                url = f"{self._hass_url}/api/config/config_entries/flow"
                headers = {"Authorization": f"Bearer {self._token}"}
                data = {
                    "handler": "esphome",
                    "show_advanced_options": False,
                }
                response = requests.post(url, headers=headers, json=data, timeout=10)
                response.raise_for_status()
                flow_id = response.json().get("flow_id")

                url = f"{self._hass_url}/api/config/config_entries/flow/{flow_id}"
                data = {"host": host, "port": port}
                response = requests.post(url, headers=headers, json=data, timeout=10)
                response.raise_for_status()
                resp = response.json()
                if resp.get("result") is None:
                    logging.error(f"ESPHome flow error {resp}")
                else:
                    return resp.get("result").get("entry_id")
            except requests.exceptions.RequestException as e:
                if attempt < retries - 1:
                    time.sleep(1)
                else:
                    raise e

    def add_esphome_device_by_name(self, name):
        """
        Add an ESPHome device to the system by its name.
        This method attempts to resolve the IP address of the ESPHome device using its
        mDNS name (e.g., "device_name.local") and then adds the device using the resolved
        IP address and the default ESPHome port (6053).
        Args:
            name (str): The mDNS name of the ESPHome device (without the ".local" suffix).
        Returns:
            str: The entry ID of the newly added ESPHome device.
        Raises:
            socket.gaierror: If the IP address resolution fails after the specified number
                             of retries.
        """

        retries = 6
        for attempt in range(retries):
            try:
                ip_address = socket.gethostbyname(f"{name}.local")
                return self.add_esphome_device(
                    ip_address, 6053
                )  # Assuming default ESPHome port is 6053
            except socket.gaierror as e:
                if attempt < retries - 1:
                    print(f"Retrying to looking for alarm... ({attempt + 1}/{retries})")
                else:
                    raise e


    def find_esphome_entry_id(self, name):
        """
        Find an existing ESPHome config entry by device name.
        Returns entry_id if found, None otherwise.
        """
        url = f"{self._hass_url}/api/config/config_entries/entry"
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            entries = response.json()
            search_name = name.replace("-", " ").lower()
            for entry in entries:
                if entry.get("domain") != "esphome":
                    continue
                title = (entry.get("title") or "").lower()
                if search_name in title or name.lower() in title:
                    return entry.get("entry_id")
        except Exception:
            pass
        return None

    def add_allow_service_esphome(self, entry_id):
        base_url = f"{self._hass_url}/api/config/config_entries/options/flow"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

        try:
            # 1Start options flow
            payload = {
                "handler": entry_id,
                "show_advanced_options": False,
            }

            response = requests.post(
                base_url,
                headers=headers,
                json=payload,
                timeout=10,
            )
            response.raise_for_status()

            response_json = response.json()
            flow_id = response_json.get("flow_id")
            if not flow_id:
                raise serializers.ValidationError(
                    {"error": "Home Assistant did not return flow_id"}
                )

            # Submit option: allow_service_calls = True
            response = requests.post(
                f"{base_url}/{flow_id}",
                headers=headers,
                json={"allow_service_calls": True},
                timeout=10,
            )
            response.raise_for_status()

            response.json()
            return {"status": "success"}

        except requests.exceptions.RequestException as err:
            raise serializers.ValidationError({"error": str(err)})

    def add_allow_google_translate(self):
        base_url = f"{self._hass_url}/api/config/config_entries/flow"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

        try:
            # 1Start options flow
            payload = {
                "handler": "google_translate",
                "show_advanced_options": False,
            }

            response = requests.post(
                base_url,
                headers=headers,
                json=payload,
                timeout=10,
            )
            response.raise_for_status()

            response_json = response.json()
            flow_id = response_json.get("flow_id")
            if not flow_id:
                raise serializers.ValidationError(
                    {"error": "Home Assistant did not return flow_id"}
                )

            # Submit option: allow_service_calls = True
            response = requests.post(
                f"{base_url}/{flow_id}",
                headers=headers,
                json={"language": "en", "tld": "com"},
                timeout=10,
            )
            response.raise_for_status()

            response.json()
            return {"status": "success"}

        except requests.exceptions.RequestException as err:
            raise serializers.ValidationError({"error": str(err)})

    def delete_device(self, entry_id):
        """
        Delete a device from the Home Assistant instance.
        Args:
            entry_id (str): The entry ID of the device to delete.
        Raises:
            requests.exceptions.HTTPError: If the HTTP request to Home Assistant fails.
        """
        url = f"{self._hass_url}/api/config/config_entries/entry/{entry_id}"
        headers = {"Authorization": f"Bearer {self._token}"}
        response = requests.delete(url, headers=headers, timeout=10)
        response.json()

    def create_automation(
        self, automation_id, name, triggers, actions, conditions=None, mode="restart"
    ):
        """
        Create a new automation in Home Assistant.

        Args:
            name (str): The name of the automation.
            triggers (list): A list of triggers for the automation.
            actions (list): A list of actions for the automation.
            conditions (list, optional): A list of conditions for the automation. Defaults to None.
            mode (str, optional): The mode of the automation. Defaults to "restart".

        Raises:
            requests.exceptions.HTTPError: If the HTTP request returned an unsuccessful status code.

        Returns:
            dict: A dictionary containing the ID of the newly created automation.
        """

        url = f"{self._hass_url}/api/config/automation/config/{automation_id}"
        headers = {"Authorization": f"Bearer {self._token}"}
        data = {
            "alias": name,
            "triggers": triggers,
            "actions": actions,
            "conditions": conditions if conditions else [],
            "mode": mode,
        }

        response = requests.post(url, headers=headers, json=data, timeout=10)
        response.raise_for_status()
        # Activate automation home assistant
        turn_on_url = f"{self._hass_url}/api/services/automation/turn_on"
        turn_on_data = {"entity_id": f"automation.{name}"}
        requests.post(turn_on_url, headers=headers, json=turn_on_data, timeout=10)

    def create_automation_script(self, automation_id, name, sequence, mode="restart"):
        """
        Create a new automation script in Home Assistant.

        Args:
            name (str): The name of the automation script .
            sequence (list): A list of actions for the automation script.
            mode (str, optional): The mode of the automation. Defaults to "restart".

        Raises:
            requests.exceptions.HTTPError: If the HTTP request returned an unsuccessful status code.

        Returns:
            dict: A dictionary containing the ID of the newly created automation script .
        """

        url = f"{self._hass_url}/api/config/script/config/{automation_id}"
        headers = {"Authorization": f"Bearer {self._token}"}
        data = {
            "alias": name,
            "sequence": sequence,
            "mode": mode,
        }
        response = requests.post(url, headers=headers, json=data, timeout=10)
        response.raise_for_status()

    def _delete_resource(self, resource_type: str, entity_id: str):
        """
        General method to delete an automation or script.
        """
        url = f"{self._hass_url}/api/config/{resource_type}/config/{entity_id}"
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            check = requests.get(url, headers=headers, timeout=10)
            if check.status_code == 200:
                response = requests.delete(url, headers=headers, timeout=10)
                response.raise_for_status()
                logging.info(f"Deleted {resource_type}: {entity_id}")
            elif check.status_code == 404:
                logging.info(f"{resource_type.title()} '{entity_id}' not found!")
            else:
                logging.warning(
                    f"Unexpected status {check.status_code} while checking {resource_type}: {check.text}"
                )
        except requests.RequestException as e:
            logging.error(f"Error deleting {resource_type} '{entity_id}': {e}")
            raise

    def delete_automation(self, automation_id: str):
        self._delete_resource("automation", automation_id)

    def delete_script(self, script_id: str):
        self._delete_resource("script", script_id)

    def add_meross_cloud(
        self,
        email,
        password,
        save_password=False,
        allow_mqtt_publish=False,
        check_firmware_updates=False,
        cloud_region="ap",
    ):
        """
        Add a Meross Cloud to the system by region, username and password.
        """
        try:
            flow_id = self.get_meross_device_flow_id().get("flow_id")
            if not flow_id:
                raise ValueError(self.get_meross_device_flow_id())

            self.send_next_step_id_to_home_assistant(flow_id)

            data = {
                "cloud_region": cloud_region,
                "email": email,
                "password": password,
                "save_password": save_password,
                "allow_mqtt_publish": allow_mqtt_publish,
                "check_firmware_updates": check_firmware_updates,
            }
            url = f"{self._hass_url}/api/config/config_entries/flow/{flow_id}"
            headers = {"Authorization": f"Bearer {self._token}"}
            response = requests.post(url, headers=headers, json=data, timeout=10)
            response.raise_for_status()
            if response.text:
                response_json = response.json()
                if "errors" in response_json and "error" in response_json["errors"]:
                    error_message = response_json["errors"]["error"]
                    raise serializers.ValidationError({"err": error_message})
                return response_json
            else:
                return {"error": response.status_code, "message": response.text}

        except requests.exceptions.RequestException as err:
            raise err

    def get_meross_device_flow_id(self):
        """
        Create a new "config entry" in Home Assistant.
        Helps start the process of installing a new Meross device or integration into Home Assistant
        """
        url = f"{self._hass_url}/api/config/config_entries/flow"
        headers = {"Authorization": f"Bearer {self._token}"}
        data = {"handler": "meross_lan", "show_advanced_options": False}
        response = requests.post(url, headers=headers, json=data, timeout=10)
        response.raise_for_status()
        return response.json()

    def send_next_step_id_to_home_assistant(self, flow_id):
        """
        This API is used to send further data during the installation of an integration into Home Assistant.
        After you call the flow creation API (/api/config/config_entries/flow), Home Assistant will return a flow_id.
        We need to use this flow_id to send additional data for the next step of the installation process.
        """

        url = f"{self._hass_url}/api/config/config_entries/flow/{flow_id}"
        headers = {"Authorization": f"Bearer {self._token}"}
        data = {"next_step_id": "profile"}
        response = requests.post(url, headers=headers, json=data, timeout=10)
        response.raise_for_status()

    def add_meross_device(self, flow_id):
        """
        Add an Meross device to the system by its name.
        """
        url = f"{self._hass_url}/api/config/config_entries/flow/{flow_id}"
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as err:
            if err.response is not None and err.response.status_code == 404:
                raise serializers.ValidationError(
                    {
                        "error": "flow_expired",
                        "message": "Meross flow is no longer valid. Refresh discovery and retry.",
                    }
                )
            raise

    def get_meross_device_discovered(self):
        """
        get an Meross device to the system by its name.
        """

        client = WebSocketClient(
            url=settings.HASS_WEBSOCKET_URL,
            access_token=self._token,
        )
        message = {"type": "config_entries/flow/progress", "id": client._generate_id()}
        devices = client.send_message(message)

        meross_devices = [
            device
            for device in devices.get("result", [])
            if device.get("handler") == "meross_lan" and device.get("flow_id")
        ]
        return meross_devices

    def get_meross_config_entries(self):
        """
        Return all Meross LAN config entries currently registered in Home Assistant.
        """
        url = f"{self._hass_url}/api/config/config_entries/entry"
        headers = {"Authorization": f"Bearer {self._token}"}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        entries = response.json()
        return [entry for entry in entries if entry.get("domain") == "meross_lan"]

    def send_message(self, message):
        """
        get an Meross device to the system by its name.
        """

        client = WebSocketClient(
            url=settings.HASS_WEBSOCKET_URL,
            access_token=self._token,
        )

        devices = client.send_message(message)
        return devices

    def get_entities(self, hass_entry_id):
        client = WebSocketClient(
            url=settings.HASS_WEBSOCKET_URL,
            access_token=self._token,
        )
        entities = client.get_entities_by_config_entry_id(hass_entry_id)
        return entities

    def get_states_entity(self, entity_id):
        url = f"{self._hass_url}/api/states/{entity_id}"
        headers = {"Authorization": f"Bearer {self._token}"}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json()

    def control_states_entity(self, entity_id, data):
        url = f"{self._hass_url}/api/states/{entity_id}"
        headers = {"Authorization": f"Bearer {self._token}"}
        response = requests.post(url, headers=headers, json=data, timeout=10)
        response.raise_for_status()
        return response.json()

    def get_logbook(self, start_time, end_time, entity_id=None):
        url = f"{self._hass_url}/api/logbook/{str(start_time)}"
        headers = {"Authorization": f"Bearer {self._token}"}
        data = {"end_time": end_time, **({"entity": entity_id} if entity_id else {})}
        response = requests.get(url, headers=headers, params=data, timeout=10)
        response.raise_for_status()
        entries = response.json()
        entries.reverse()  # reverse list in place
        return entries

    def automation_trigger(self, hass_entry_id):
        client = WebSocketClient(
            url=settings.HASS_WEBSOCKET_URL,
            access_token=self._token,
        )
        automation_trigger = client.get_device_automation_trigger(hass_entry_id)
        return automation_trigger

    def call_service(self, domain_service, data):
        url = f"{self._hass_url}/api/services/{domain_service}"
        headers = {"Authorization": f"Bearer {self._token}"}
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        return response.json()

    def get_media_player_entity(self, hass_entry_id):
        entities = self.get_entities(hass_entry_id)
        data = entities["result"]
        speaker = next(
            (e for e in data.get("entity", []) if e.startswith("media_player.")), None
        )
        return speaker

    def delete_state_entity(self, entity_id):
        url = f"{self._hass_url}/api/states/{entity_id}"
        headers = {"Authorization": f"Bearer {self._token}"}
        response = requests.delete(url, headers=headers, timeout=10)
        if response.status_code not in [200, 201, 404]:
            response.raise_for_status()
        return response.status_code

    def purge_ring_device_traces(self, camera):
        client = WebSocketClient(
            url=settings.HASS_WEBSOCKET_URL,
            access_token=self._token,
        )
        try:
            identifiers = {
                str(value).lower()
                for value in [
                    getattr(camera, "ring_device_id", None),
                    getattr(camera, "ring_id", None),
                    getattr(camera, "slug_name", None),
                    getattr(camera, "name", None),
                ]
                if value
            }
            if not identifiers:
                return

            entities = client.list_entity_registry()
            matched_entities = [
                entity
                for entity in entities
                if self._ring_registry_entry_matches(entity, identifiers)
            ]

            matched_device_ids = set()
            for entity in matched_entities:
                entity_id = entity.get("entity_id")
                if not entity_id:
                    continue
                related = client.search_related("entity", entity_id)
                matched_device_ids.update(related.get("device", []))

            devices = client.list_device_registry()
            matched_devices = [
                device
                for device in devices
                if device.get("id") in matched_device_ids
                or self._ring_registry_entry_matches(device, identifiers)
            ]

            for entity in matched_entities:
                entity_id = entity.get("entity_id")
                if not entity_id:
                    continue
                client.remove_entity_registry(entity_id)
                self.delete_state_entity(entity_id)

            for device in matched_devices:
                device_id = device.get("id")
                if device_id:
                    client.remove_device_registry(device_id)
        finally:
            client.close()

    def _ring_registry_entry_matches(self, entry, identifiers):
        if not identifiers:
            return False

        def _contains(value):
            if value is None:
                return False
            if isinstance(value, (list, tuple, set)):
                return any(_contains(item) for item in value)
            if isinstance(value, dict):
                return any(_contains(item) for item in value.values())
            text = str(value).lower()
            return any(identifier in text for identifier in identifiers)

        for field in [
            "entity_id",
            "unique_id",
            "original_name",
            "name",
            "name_by_user",
            "model",
            "manufacturer",
            "identifiers",
            "connections",
        ]:
            if _contains(entry.get(field)):
                return True
        return False


class HomeAssistantUnavailable(APIException):
    status_code = 503
    default_detail = "Home Assistant is unavailable."
    default_code = "home_assistant_unavailable"


class InterfaceHASSView(GenericAPIView):
    serializer_class = []

    def getHassClient(self):
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
        return client

    def enable_service_calls(self, entry_id):
        """
        Enable allow_service_calls for an ESPHome config entry.
        This allows the device to make Home Assistant service calls.
        """
        url = f"{self._hass_url}/api/config/config_entries/options/{entry_id}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json"
        }
        data = {"allow_service_calls": True}
        
        try:
            response = requests.post(url, headers=headers, json=data, timeout=10)
            response.raise_for_status()
            return True
        except Exception as exc:
            print(f"Failed to enable service calls for {entry_id}: {exc}")
            return False

