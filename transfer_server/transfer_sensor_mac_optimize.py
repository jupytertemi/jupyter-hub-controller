#!/usr/bin/env python3
"""
BLE Config TCP Server — v1.6 patched
- ESP32 connects via TCP :4444
- python-app-controller connects via TCP :4444
- Controller sends commands
- Server relays to ESP

v1.6 PATCH (Halo onboard auto-register):
- On every successful ESP "register" event, POST the registration payload
  + peer_ip to the hub Django at WEBHOOK_URL. Hub then auto-creates the
  AlarmDevice row, replacing the broken POST /api/alarms create flow.
- Webhook is fire-and-forget — TCP register flow is unaffected if the hub
  Django is down. (Halo heartbeat re-fires the webhook every 30s anyway.)
"""

import os
import socket
import threading
import json
import logging
import time
import urllib.request
import urllib.error
from typing import Dict, Optional

# ================= CONFIG =================
BIND_ADDRESS = "0.0.0.0"
TCP_PORT = 4444

# v1.6: webhook URL for Halo register events. Defaults to localhost:80
# (HAProxy → Django on docker bridge). Override via env var if needed.
WEBHOOK_URL = os.environ.get(
    "HALO_REGISTER_WEBHOOK_URL",
    "http://haproxy-service:80/api/internal/halo-register",
)
WEBHOOK_TIMEOUT = float(os.environ.get("HALO_REGISTER_WEBHOOK_TIMEOUT", "3.0"))

# v1.6: dedup window — suppress webhook for the same slug within this many
# seconds. Halos heartbeat the register payload every 30 s; once Django
# has the row, repeated webhooks just hit the not_pending guard and 403.
# Suppressing them locally keeps logs clean and saves HTTP calls.
WEBHOOK_DEDUP_SEC = float(os.environ.get("HALO_REGISTER_WEBHOOK_DEDUP_SEC", "120"))
_last_webhook_fired: Dict[str, float] = {}
_dedup_lock = threading.Lock()

# ================= LOGGING =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("BLE-TCP")

# ================= GLOBAL STATE =================
connected_devices: Dict[str, dict] = {}
devices_lock = threading.Lock()


# ================= ESP / CLIENT HANDLER =================
class ClientHandler(threading.Thread):
    def __init__(self, sock: socket.socket, address: tuple):
        super().__init__(daemon=True)
        self.sock = sock
        self.address = address
        self.device_name: Optional[str] = None
        self.role: Optional[str] = None
        self.running = True

    def run(self):
        logger.info(f"🔌 New TCP connection from {self.address}")
        buffer = ""

        try:
            while self.running:
                data = self.sock.recv(1024)
                if not data:
                    break

                text = data.decode("utf-8")
                logger.debug(f"RAW {self.address}: {text!r}")

                buffer += text
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if line:
                        self.handle_message(line)

        except Exception as e:
            logger.error(f"❌ Error {self.address}: {e}")
        finally:
            self.cleanup()

    # ================= MESSAGE HANDLING =================
    def handle_message(self, msg: str):
        try:
            data = json.loads(msg)
        except json.JSONDecodeError:
            logger.warning(f"⚠️ Invalid JSON from {self.address}: {msg}")
            return

        action = data.get("action")

        # ---------- REGISTER ----------
        if action == "register":
            self.device_name = data.get("device", f"unknown_{self.address[0]}")
            self.role = data.get("role", "esp")
            if "controller" in self.device_name.lower():
                self.role = "controller"
            elif "alarm" in self.device_name.lower():
                self.role = "esp"

            with devices_lock:
                connected_devices[self.device_name] = {
                    "handler": self,
                    "role": self.role,
                    "address": self.address,
                }

            logger.info(
                f"✅ Registered {self.device_name} "
                f"(role={self.role}, addr={self.address})"
            )
            self.log_devices()

            # v1.6: fire webhook to hub Django for ESP registers (auto-create
            # AlarmDevice row). Fire-and-forget on a background thread so a
            # slow / unreachable Django doesn't block TCP register flow.
            if self.role == "esp":
                threading.Thread(
                    target=self._fire_webhook,
                    args=(data,),
                    daemon=True,
                ).start()
            return

        # ---------- CONTROLLER COMMAND ----------
        if action in ("add", "remove", "clear", "factory_reset", "reboot", "restart", "reset_request"):
            logger.info(
                f"📨 Command from {self.device_name}: {data}"
            )

            status = data.get("status", None)
            result = data.get("result", None)

            if self.role == "controller":
                logger.info(f"🚀 Relaying command from {self.device_name} to ESP devices")
                self.relay_to_esp(data)
                return
            elif self.role == "esp":
                logger.info(f"🚀 Relaying response from {self.device_name} to controller devices")
                self.relay_to_controller(data)
                return
            else:
                logger.warning(
                    f"🚫 Ignored command from {self.device_name}"
                )
                return

        # ---------- ESP RESPONSE ----------
        logger.info(f"📥 Message from {self.device_name}: {data}")

    # ================= v1.6 WEBHOOK =================
    def _fire_webhook(self, register_data: dict):
        """POST register payload + peer_ip to hub Django.

        Auto-creates AlarmDevice row via /api/internal/halo-register.
        Fire-and-forget; failures are logged but don't affect TCP flow.

        Dedup: suppress repeated calls for the same slug within
        WEBHOOK_DEDUP_SEC. Halo heartbeats every 30s — once Django has
        the row, additional webhooks just hit the not_pending guard.
        """
        slug = self.device_name or ""
        now = time.monotonic()

        # Dedup gate — quick exit if we fired for this slug recently
        with _dedup_lock:
            last = _last_webhook_fired.get(slug, 0.0)
            if (now - last) < WEBHOOK_DEDUP_SEC:
                logger.debug(
                    f"🪝 Webhook suppressed (dedup) for {slug} "
                    f"({now - last:.1f}s since last fire)"
                )
                return
            _last_webhook_fired[slug] = now

        try:
            payload = dict(register_data)
            payload["peer_ip"] = self.address[0]
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                WEBHOOK_URL,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Webhook-Source": "transfer_server-v1.6",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=WEBHOOK_TIMEOUT) as resp:
                code = resp.status
                if 200 <= code < 300:
                    logger.info(
                        f"🪝 Webhook OK ({code}) for {slug} "
                        f"peer_ip={self.address[0]}"
                    )
                else:
                    logger.warning(
                        f"🪝 Webhook unexpected status {code} for {slug}"
                    )
        except urllib.error.HTTPError as exc:
            # 403 not_pending is EXPECTED — Halo registered without app
            # initiating /api/halo/onboard-payload (slug not in pending).
            # Either: (a) factory-fresh Halo waiting for onboard, or
            # (b) post-onboard heartbeat after the pending entry expired.
            # Don't log as error.
            if exc.code == 403:
                logger.debug(
                    f"🪝 Webhook 403 (slug not pending) for {slug}"
                )
            else:
                logger.warning(
                    f"🪝 Webhook HTTP {exc.code} for {slug}: {exc.reason}"
                )
        except Exception as e:
            # Conn refused / DNS / timeout — Halo TCP register flow is
            # unaffected. Halo heartbeat in 30 s + dedup window means
            # we'll retry on the natural cadence.
            logger.warning(
                f"🪝 Webhook failed for {slug} "
                f"({WEBHOOK_URL}): {e}"
            )
            # Clear dedup so next heartbeat retries
            with _dedup_lock:
                _last_webhook_fired.pop(slug, None)

    # ================= RELAY =================
    def relay_to_esp(self, command: dict):
        sent = 0
        with devices_lock:
            for name, info in connected_devices.items():
                if info.get("role") == "esp" or "alarm" in name.lower():
                    ok = info["handler"].send(command)
                    if ok:
                        sent += 1

        logger.info(f"🚀 Relay complete: sent to {sent} ESP device(s)")

    def relay_to_controller(self, command: dict):
        sent = 0
        with devices_lock:
            for name, info in connected_devices.items():
                if info.get("role") == "controller":
                    ok = info["handler"].send(command)
                    if ok:
                        sent += 1

        logger.info(f"🚀 Relay complete: sent to {sent} controller device(s)")

    # ================= SEND =================
    def send(self, payload: dict) -> bool:
        try:
            msg = json.dumps(payload) + "\n"
            self.sock.sendall(msg.encode("utf-8"))
            logger.info(f"➡️ Sent to {self.device_name}: {payload}")
            return True
        except Exception as e:
            logger.error(f"❌ Send failed to {self.device_name}: {e}")
            return False

    # ================= CLEANUP =================
    def cleanup(self):
        self.running = False
        if self.device_name:
            with devices_lock:
                connected_devices.pop(self.device_name, None)
            # v1.6: clear dedup so a Halo that disconnects + reconnects
            # immediately (e.g. post-factory-reset reboot) gets a fresh
            # webhook fire on the new register.
            with _dedup_lock:
                _last_webhook_fired.pop(self.device_name, None)
            logger.info(f"🔌 Disconnected: {self.device_name}")
            self.log_devices()

        try:
            self.sock.close()
        except:
            pass

    # ================= LOG DEVICES =================
    @staticmethod
    def log_devices():
        with devices_lock:
            logger.info("📡 Connected devices:")
            for name, info in connected_devices.items():
                logger.info(
                    f"   - {name} "
                    f"role={info['role']} "
                    f"addr={info['address']}"
                )


# ================= TCP SERVER =================
def start_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((BIND_ADDRESS, TCP_PORT))
    server.listen(10)

    logger.info(f"🚀 TCP Server listening on {BIND_ADDRESS}:{TCP_PORT}")
    logger.info(f"🪝 Halo register webhook → {WEBHOOK_URL}")

    while True:
        client, addr = server.accept()
        handler = ClientHandler(client, addr)
        handler.start()


# ================= MAIN =================
if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("BLE CONFIG TCP SERVER STARTED (v1.6 — Halo onboard webhook)")
    logger.info("=" * 60)
    start_server()
