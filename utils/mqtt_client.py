import logging
from typing import Optional

import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)


class MQTTClient:
    def __init__(
        self,
        host: str,
        port: int = 1883,
        username: Optional[str] = None,
        password: Optional[str] = None,
        client_id: str = "alarm-api",
        keepalive: int = 60,
        use_tls: bool = False,
        ca_certs: Optional[str] = None,
    ):
        self.host = host
        self.port = port
        self.keepalive = keepalive

        self.client = mqtt.Client(client_id=client_id, clean_session=True)

        if username and password:
            self.client.username_pw_set(username, password)

        if use_tls:
            self.client.tls_set(ca_certs=ca_certs)

        # Callbacks (optional but useful)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_publish = self._on_publish

    # ---------- Callbacks ----------
    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info("MQTT connected successfully")
        else:
            logger.error(f"MQTT connection failed, rc={rc}")

    def _on_disconnect(self, client, userdata, rc):
        logger.warning(f"MQTT disconnected, rc={rc}")

    def _on_publish(self, client, userdata, mid):
        logger.debug(f"MQTT message published, mid={mid}")

    # ---------- Public API ----------
    def connect(self):
        self.client.connect(self.host, self.port, self.keepalive)
        self.client.loop_start()

    def publish(
        self,
        topic: str,
        payload: str,
        qos: int = 1,
        retain: bool = False,
    ):
        logger.info(f"Publishing to {topic}: {payload}")
        self.client.publish(topic, payload, qos=qos, retain=retain)

    def close(self):
        self.client.loop_stop()
        self.client.disconnect()
