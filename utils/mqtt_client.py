import logging
import time
from typing import Optional

import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)

# 2026-05-07: bounded reconnect on initial connect, plus paho's built-in
# post-connection reconnect via reconnect_delay_set. Smooths brief EMQX
# broker hiccups without blocking the request handler indefinitely on a
# truly-down broker.
_DEFAULT_INITIAL_RETRIES = 3
_RECONNECT_MIN_DELAY_S = 1
_RECONNECT_MAX_DELAY_S = 60


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

        # Auto-reconnect after a successful initial connection.
        # paho honours this in loop_start(); kicks in when broker drops mid-session.
        self.client.reconnect_delay_set(
            min_delay=_RECONNECT_MIN_DELAY_S,
            max_delay=_RECONNECT_MAX_DELAY_S,
        )

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
        # rc != 0 means unexpected disconnect — paho will auto-reconnect via
        # reconnect_delay_set as long as loop_start() is running.
        if rc != 0:
            logger.warning(f"MQTT disconnected unexpectedly, rc={rc}; auto-reconnect armed")
        else:
            logger.info("MQTT disconnected cleanly")

    def _on_publish(self, client, userdata, mid):
        logger.debug(f"MQTT message published, mid={mid}")

    # ---------- Public API ----------
    def connect(self, max_initial_retries: int = _DEFAULT_INITIAL_RETRIES):
        """
        Connect to broker with bounded retry on initial failure.

        Once connected, paho's loop_start() + reconnect_delay_set handle
        post-disconnect reconnection automatically.

        Raises the last exception if all initial attempts fail.
        """
        last_exc = None
        for attempt in range(1, max_initial_retries + 1):
            try:
                self.client.connect(self.host, self.port, self.keepalive)
                self.client.loop_start()
                if attempt > 1:
                    logger.info(f"MQTT connect succeeded on attempt {attempt}")
                return
            except Exception as exc:
                last_exc = exc
                if attempt < max_initial_retries:
                    sleep_s = min(2 ** (attempt - 1), 8)  # 1, 2, 4 — total ~7s
                    logger.warning(
                        f"MQTT connect attempt {attempt}/{max_initial_retries} "
                        f"to {self.host}:{self.port} failed ({exc}); retrying in {sleep_s}s"
                    )
                    time.sleep(sleep_s)
        logger.error(
            f"MQTT connect failed after {max_initial_retries} attempts "
            f"to {self.host}:{self.port}: {last_exc}"
        )
        raise last_exc

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
