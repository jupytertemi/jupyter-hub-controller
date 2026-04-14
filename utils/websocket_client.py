import json

from websocket import create_connection


class WebSocketClient:
    _RELATED_RESULT_KEYS = (
        "config_entry",
        "device",
        "entity",
        "area",
        "automation",
        "script",
        "group",
        "scene",
        "integration",
    )

    def __init__(self, url, access_token):
        self.access_token = access_token
        self.ws = create_connection(url)
        self._current_id = 1
        first_response = json.loads(self.ws.recv())

        # Wait for "auth_required" message from Home Assistant
        if first_response.get("type") != "auth_required":
            raise Exception("Expected 'auth_required', but received:", first_response)

        # Send verification
        self.on_auth()

        # Waiting for response "auth_ok"
        auth_response = json.loads(self.ws.recv())
        if auth_response.get("type") != "auth_ok":
            raise Exception("Authentication failed:", auth_response)

    def on_auth(self):
        """Send token for WebSocket authentication."""
        auth_message = {"type": "auth", "access_token": self.access_token}
        self.ws.send(json.dumps(auth_message))

    def send_message(self, message):
        """Send WebSocket message after successful authentication."""
        self.ws.send(json.dumps(message))
        response = self.ws.recv()
        return json.loads(response)

    def close(self):
        self.ws.close()

    def _generate_id(self):
        self._current_id += 1
        return self._current_id

    def _normalize_related_response(self, response):
        normalized = {
            "id": response.get("id", self._current_id),
            "type": response.get("type", "result"),
            "success": response.get("success", True),
            "result": {},
        }
        raw_result = response.get("result") or {}
        for key in self._RELATED_RESULT_KEYS:
            value = raw_result.get(key)
            normalized["result"][key] = value if isinstance(value, list) else []
        return normalized

    def get_device_registry(self, config_entry_id):
        message = {"id": self._generate_id(), "type": "config/device_registry/list"}
        response = self.send_message(message)
        devices = response.get("result", [])
        for device in devices:
            if config_entry_id in device.get("config_entries", []):
                return device
        return None

    def list_device_registry(self):
        message = {"id": self._generate_id(), "type": "config/device_registry/list"}
        response = self.send_message(message)
        return response.get("result", [])

    def list_entity_registry(self):
        message = {"id": self._generate_id(), "type": "config/entity_registry/list"}
        response = self.send_message(message)
        return response.get("result", [])

    def search_related(self, item_type, item_id):
        message = {
            "id": self._generate_id(),
            "type": "search/related",
            "item_type": item_type,
            "item_id": item_id,
        }
        response = self.send_message(message)
        return response.get("result", {})

    def remove_entity_registry(self, entity_id):
        message = {
            "id": self._generate_id(),
            "type": "config/entity_registry/remove",
            "entity_id": entity_id,
        }
        return self.send_message(message)

    def remove_device_registry(self, device_id):
        message = {
            "id": self._generate_id(),
            "type": "config/device_registry/remove",
            "device_id": device_id,
        }
        return self.send_message(message)

    def get_entities_by_config_entry_id(self, config_entry_id):
        # First get device_id from config_entry
        device = self.get_device_registry(config_entry_id)
        if not device:
            return self._normalize_related_response({})
        device_id = device["id"]
        message = {
            "id": self._generate_id(),
            "type": "search/related",
            "item_type": "device",
            "item_id": device_id,
        }
        response = self.send_message(message)
        return self._normalize_related_response(response)

    def get_device_automation_trigger(self, config_entry_id):
        # First get device_id from config_entry
        device = self.get_device_registry(config_entry_id)
        if not device:
            return None
        device_id = device["id"]
        message = {
            "id": self._generate_id(),
            "type": "device_automation/trigger/list",
            "device_id": device_id,
        }
        response = self.send_message(message)
        return response
