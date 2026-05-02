#!/usr/bin/env python3
"""Halo offboard 2FA factory-reset simulator.

Validates the full 2FA chain end-to-end without a real Halo, without an
iPhone. Plays both sides:

  * Fake-Halo MQTT — subscribes to /{slug}/recovery, replies with
    `pending` + nonce on /recovery/status when factory_reset arrives,
    then watches for `confirm_factory_reset` and `cancel_factory_reset`.

  * Fake admin — calls DELETE /api/alarms/{id}, then waits for the
    `/halo_offboard_2fa_pending` MQTT message (the LA push trigger),
    then "presses Confirm" by calling /api/halo/recovery/confirm.

Why this works without firmware: per the firmware brief, the Halo's
recovery state machine is implemented IN firmware. Our test exercises
the BACKEND — that the hub publishes the right MQTT topics, fires the
LA push, and accepts the confirm callback. The firmware's NVS-erase
half is firmware-validated separately.

Usage:
    # Use a slug that doesn't need to match a real Halo. The simulator
    # will create an AlarmDevice via the onboard sim's webhook path so
    # there's a real DB row to DELETE.
    python tools/sim/halo_offboard_sim.py \\
        --hub 192.168.1.161 \\
        --hub-secret "$HUB_SECRET" \\
        --mqtt-pass "$MQTT_CONTROLLER_PASSWORD"
"""
from __future__ import annotations

import argparse
import base64
import json
import random
import socket
import sys
import threading
import time

import paho.mqtt.client as mqtt
import requests

# ANSI colors
RESET = "\033[0m"; DIM = "\033[2m"; BOLD = "\033[1m"
APP = "\033[92m"; HUB = "\033[93m"; MQTT = "\033[95m"
ERR = "\033[91m"; OK = "\033[92m"


class FakeHalo:
    """Plays the Halo's MQTT side of the 2FA flow.

    Subscribes to /{slug}/recovery. When `factory_reset` arrives:
      * Verifies the secret matches (else publishes denied/invalid_secret).
      * Generates a random uint32 nonce.
      * Publishes `pending` on /{slug}/recovery/status.
      * Stores the nonce; expects the next confirm_factory_reset to match.
    """

    def __init__(self, broker_host: str, broker_port: int, mqtt_user: str,
                 mqtt_pass: str, slug: str, device_secret: str, serial: str):
        self.slug = slug
        self.device_secret = device_secret
        self.serial = serial
        self.recovery_topic = f"/{slug}/recovery"
        self.status_topic = f"/{slug}/recovery/status"
        self.nonce: int | None = None
        self.received_factory_reset = threading.Event()
        self.received_confirm = threading.Event()
        self.received_cancel = threading.Event()
        self.lock = threading.Lock()
        self.events: list[dict] = []  # full audit trail

        self.client = mqtt.Client(client_id=f"fake-halo-{slug}",
                                  clean_session=True)
        self.client.username_pw_set(mqtt_user, mqtt_pass)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.connect(broker_host, broker_port, keepalive=30)
        self.client.loop_start()

    def _on_connect(self, client, _ud, _flags, rc):
        if rc != 0:
            print(f"{ERR}[fake-Halo] mqtt connect rc={rc}{RESET}")
            return
        client.subscribe(self.recovery_topic, qos=1)
        print(f"{MQTT}[fake-Halo] subscribed {self.recovery_topic}{RESET}")

    def _on_message(self, _client, _ud, msg):
        try:
            data = json.loads(msg.payload.decode("utf-8"))
        except Exception:
            return
        with self.lock:
            self.events.append({"ts": time.monotonic(),
                                "topic": msg.topic, "data": data})
        cmd = data.get("command")
        print(f"{MQTT}[fake-Halo] rx {msg.topic}: {data}{RESET}")

        if cmd == "factory_reset":
            self.received_factory_reset.set()
            secret = data.get("secret", "")
            if secret != self.device_secret:
                self._publish({"factory_reset": "denied",
                               "reason": "invalid_secret"})
                return
            self.nonce = random.randint(1, 2**32 - 1)
            self._publish({
                "factory_reset": "pending",
                "nonce": self.nonce,
                "expires_in": 60,
                "serial": self.serial,
            })
        elif cmd == "confirm_factory_reset":
            self.received_confirm.set()
            recv_nonce = data.get("nonce")
            if recv_nonce == self.nonce:
                self._publish({"factory_reset": "confirmed",
                               "status": "resetting"})
            else:
                self._publish({"factory_reset": "denied",
                               "reason": "invalid_nonce"})
        elif cmd == "cancel_factory_reset":
            self.received_cancel.set()
            self._publish({"factory_reset": "cancelled"})

    def _publish(self, payload: dict):
        msg = json.dumps(payload)
        self.client.publish(self.status_topic, msg, qos=1)
        print(f"{MQTT}[fake-Halo] tx {self.status_topic}: {payload}{RESET}")

    def stop(self):
        self.client.loop_stop()
        self.client.disconnect()


class LaPushWatcher:
    """Subscribes to /halo_offboard_2fa_pending so the test can verify
    the hub published the LA-trigger payload."""

    def __init__(self, broker_host, broker_port, mqtt_user, mqtt_pass):
        self.received: list[dict] = []
        self.lock = threading.Lock()
        self.client = mqtt.Client(client_id=f"la-watch-{int(time.time())}",
                                  clean_session=True)
        self.client.username_pw_set(mqtt_user, mqtt_pass)
        self.client.on_message = self._on_msg
        self.client.connect(broker_host, broker_port, keepalive=30)
        self.client.subscribe("/halo_offboard_2fa_pending", qos=1)
        self.client.loop_start()
        time.sleep(0.5)

    def _on_msg(self, _c, _u, msg):
        try:
            data = json.loads(msg.payload.decode("utf-8"))
        except Exception:
            return
        with self.lock:
            self.received.append({"ts": time.monotonic(), "data": data})
        print(f"{MQTT}[la-watch] rx /halo_offboard_2fa_pending: {data}{RESET}")

    def wait(self, timeout=5.0) -> dict | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                if self.received:
                    return self.received[-1]["data"]
            time.sleep(0.05)
        return None

    def stop(self):
        self.client.loop_stop()
        self.client.disconnect()


# ---- onboard helper (reuse the onboard simulator's transfer_server flow) ----

class TransferServerRegister:
    """Tiny helper that opens TCP :4444, sends a register payload, leaves
    the connection open. The hub webhook auto-creates an AlarmDevice."""

    def __init__(self, hub_ip: str, slug: str, device_secret: str):
        self.hub_ip = hub_ip
        self.slug = slug
        self.device_secret = device_secret
        self.sock: socket.socket | None = None

    def __enter__(self):
        self.sock = socket.create_connection((self.hub_ip, 4444), timeout=5)
        # Register controller-style first so transfer_server treats the
        # next register as ESP. Actually transfer_server keys role from
        # device name — "alarm" prefix → esp.
        register = {
            "action": "register",
            "device": self.slug,
            "type": "esp",
            "role": "esp",
            "device_secret": self.device_secret,
            "version_fw": "v2.19.1-sim",
            "mac_address": "ea:a3:24",
            "ip_address": "127.0.0.1",
        }
        self.sock.sendall((json.dumps(register) + "\n").encode())
        time.sleep(0.5)
        return self

    def __exit__(self, *_):
        try:
            self.sock.close()
        except Exception:
            pass


class HubAPI:
    def __init__(self, hub_ip: str, hub_secret: str):
        self.base = f"http://{hub_ip}"
        self.auth = base64.b64encode(f"hub:{hub_secret}".encode()).decode()

    def _hdrs(self):
        return {"Authorization": f"Basic {self.auth}",
                "Content-Type": "application/json"}

    def get(self, path):
        return requests.get(f"{self.base}{path}", headers=self._hdrs(),
                            timeout=10)

    def delete(self, path):
        return requests.delete(f"{self.base}{path}", headers=self._hdrs(),
                               timeout=15)

    def post(self, path, body):
        return requests.post(f"{self.base}{path}", headers=self._hdrs(),
                             json=body, timeout=10)

    def onboard_payload(self, slug):
        return self.post("/api/halo/onboard-payload", {"halo_slug": slug})

    def find_alarm_id(self, slug):
        r = self.get("/api/alarms")
        for row in r.json().get("results", []):
            if row.get("identity_name") == slug:
                return row["id"]
        return None


# ---- main test ----

def step(name):
    print(f"\n{DIM}{'─' * 70}{RESET}\n{BOLD}{name}{RESET}")


def ok(msg):
    print(f"{OK}✓ {msg}{RESET}")


def fail(msg):
    print(f"{ERR}✗ {msg}{RESET}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--hub", default="192.168.1.161")
    p.add_argument("--hub-secret", required=True)
    p.add_argument("--mqtt-user", default="controller")
    p.add_argument("--mqtt-pass", required=True)
    p.add_argument("--slug",
                   default=f"jupyter-alarm-off{int(time.time()) % 100000:05d}")
    args = p.parse_args()

    api = HubAPI(args.hub, args.hub_secret)
    slug = args.slug
    serial = f"JUP-OUTDR-{slug.split('-')[-1].upper()}"

    results = []

    # -------- 1. Onboard a fake Halo so there's an AlarmDevice to delete --
    step("1. Pre-flight: create AlarmDevice via onboard webhook")
    pl = api.onboard_payload(slug)
    if pl.status_code != 200:
        fail(f"onboard-payload failed: {pl.status_code} {pl.text[:200]}")
        return 2
    payload = pl.json()
    api_token = payload["halo_api_token"]
    device_secret = payload.get("device_secret") or "0" * 64

    # The webhook needs a TCP register from transfer_server. We don't have
    # the Halo's actual device_secret yet — make one up; transfer_server
    # forwards whatever we send and the hub stores it on the AlarmDevice.
    fake_secret = "deadbeef" * 8  # 64 hex chars
    with TransferServerRegister(args.hub, slug, fake_secret):
        # Wait for hub to create AlarmDevice via webhook
        deadline = time.monotonic() + 10
        alarm_id = None
        while time.monotonic() < deadline:
            alarm_id = api.find_alarm_id(slug)
            if alarm_id:
                break
            time.sleep(0.5)

    if not alarm_id:
        fail("AlarmDevice was not created by webhook")
        return 2
    ok(f"AlarmDevice created id={alarm_id} slug={slug}")
    results.append(("AlarmDevice created", True))

    # -------- 2. Wire fake-Halo MQTT subscriber + LA-push watcher --------
    step("2. Wire fake-Halo MQTT subscriber + LA push watcher")
    halo = FakeHalo(args.hub, 1883, args.mqtt_user, args.mqtt_pass,
                    slug, fake_secret, serial)
    la = LaPushWatcher(args.hub, 1883, args.mqtt_user, args.mqtt_pass)
    time.sleep(1.0)
    ok("Both subscribers connected")

    # -------- 3. DELETE /api/alarms/{id} → triggers 2FA flow ------------
    step("3. DELETE /api/alarms/{id} (admin offboards)")
    t0 = time.monotonic()
    r = api.delete(f"/api/alarms/{alarm_id}")
    elapsed = time.monotonic() - t0
    print(f"{APP}[app→hub] DELETE → HTTP {r.status_code} in {elapsed*1000:.0f}ms{RESET}")
    if r.status_code != 200:
        fail(f"DELETE failed: {r.text[:200]}")
        halo.stop(); la.stop()
        return 2
    body = r.json()
    print(f"  {DIM}body: {json.dumps(body, indent=2)}{RESET}")

    # -------- 4. Verify Halo received factory_reset --------------------
    step("4. Verify fake-Halo received factory_reset command")
    if halo.received_factory_reset.wait(timeout=2.0):
        ok("fake-Halo received factory_reset")
        results.append(("Halo received factory_reset", True))
    else:
        fail("fake-Halo did NOT receive factory_reset")
        results.append(("Halo received factory_reset", False))

    if not body.get("factory_reset_dispatched"):
        fail(f"backend reported factory_reset_dispatched=false: note={body.get('note')}")
        results.append(("backend dispatched 2FA", False))
    else:
        ok(f"backend reported dispatched, nonce={body.get('factory_reset_nonce')}")
        results.append(("backend dispatched 2FA", True))

    # -------- 5. Verify LA push payload was published ------------------
    step("5. Verify hub published /halo_offboard_2fa_pending (LA trigger)")
    la_payload = la.wait(timeout=3.0)
    if not la_payload:
        fail("no /halo_offboard_2fa_pending message received")
        results.append(("LA push payload published", False))
    else:
        ok(f"LA push payload received: nonce={la_payload.get('nonce')} "
           f"serial={la_payload.get('serial')}")
        results.append(("LA push payload published", True))
        # Sanity-check fields
        for k in ("slug", "alarm_id", "nonce", "serial", "expires_at",
                  "title", "body"):
            if k not in la_payload:
                fail(f"LA payload missing {k}")
                results.append((f"LA payload has {k}", False))

    # -------- 6. "Press Confirm" — call /api/halo/recovery/confirm -----
    step("6. Admin presses Confirm — POST /api/halo/recovery/confirm")
    if not la_payload:
        fail("skipping — no nonce captured from LA push")
        results.append(("Confirm endpoint published", False))
    else:
        confirm = api.post("/api/halo/recovery/confirm",
                           {"slug": slug, "nonce": la_payload["nonce"]})
        print(f"{APP}[app→hub] POST /confirm → HTTP {confirm.status_code} "
              f"body={confirm.text[:100]}{RESET}")
        if confirm.status_code == 200 and confirm.json().get("published"):
            ok("hub published confirm_factory_reset")
            results.append(("Confirm endpoint published", True))
        else:
            fail(f"confirm endpoint failed: {confirm.text[:200]}")
            results.append(("Confirm endpoint published", False))

    # -------- 7. Verify Halo received confirm_factory_reset ------------
    step("7. Verify fake-Halo received confirm_factory_reset")
    if halo.received_confirm.wait(timeout=2.0):
        ok("fake-Halo received confirm_factory_reset")
        results.append(("Halo received confirm", True))
    else:
        fail("fake-Halo did NOT receive confirm_factory_reset")
        results.append(("Halo received confirm", False))

    # -------- 8. Verify nonce match (in audit log) ---------------------
    step("8. Audit: confirm command nonce matches Halo's pending nonce")
    confirms = [e for e in halo.events
                if e["data"].get("command") == "confirm_factory_reset"]
    if confirms and confirms[-1]["data"].get("nonce") == halo.nonce:
        ok(f"nonce matches: {halo.nonce}")
        results.append(("Nonce matches", True))
    else:
        fail(f"nonce mismatch — halo issued {halo.nonce}, "
             f"got {confirms[-1]['data'].get('nonce') if confirms else None}")
        results.append(("Nonce matches", False))

    # -------- Cleanup ---------------------------------------------------
    halo.stop()
    la.stop()
    # AlarmDevice already deleted by step 3.

    # -------- Report ---------------------------------------------------
    print(f"\n{BOLD}{'═' * 70}{RESET}")
    print(f"{BOLD}OFFBOARD 2FA SIMULATION REPORT — slug={slug}{RESET}")
    print('═' * 70)
    passed = sum(1 for _, ok_ in results if ok_)
    failed = len(results) - passed
    for name, ok_ in results:
        mark = f"{OK}✓" if ok_ else f"{ERR}✗"
        print(f"  {mark} {name}{RESET}")
    print('─' * 70)
    if failed == 0:
        print(f"{OK}{BOLD}ALL {passed} CHECKS PASSED{RESET}")
        return 0
    print(f"{ERR}{BOLD}{failed} CHECK(S) FAILED, {passed} PASSED{RESET}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
