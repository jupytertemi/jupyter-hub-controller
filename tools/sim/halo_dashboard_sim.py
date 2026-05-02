#!/usr/bin/env python3
"""Halo dashboard interaction simulator.

Connects to the hub's MQTT broker as a fake Halo subscriber to verify what
MQTT messages the firmware would receive when the user toggles dashboard
controls (alarm modes, occupancy illusion, unusual sound, voice AI, manual
alarm). For each dashboard action, asserts:

    1. Hub HTTP API accepts the request (200/204)
    2. Within 3s, an MQTT message arrives on the expected topic
    3. The MQTT payload shape matches what firmware expects

Intended to run AFTER an onboard simulation has produced an AlarmDevice
row, so the hub has a real (slug, id) pair to address.

Usage:
    python tools/sim/halo_dashboard_sim.py \\
        --hub 192.168.1.161 \\
        --hub-secret "$(...)" \\
        --slug jupyter-alarm-sim12626 \\
        --alarm-id 3 \\
        --mqtt-pass "$(...)"

Exit code: 0 if every dashboard action produced the expected MQTT signal.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import threading
import time
from collections import defaultdict

import paho.mqtt.client as mqtt
import requests

# ANSI colors
RESET = "\033[0m"; DIM = "\033[2m"; BOLD = "\033[1m"
APP_C = "\033[92m"; HUB_C = "\033[93m"; MQTT_C = "\033[95m"
ERR_C = "\033[91m"; OK_C = "\033[92m"


# ---------------------------------------------------------------------------
# Fake Halo MQTT subscriber
# ---------------------------------------------------------------------------
class FakeHaloMqttSubscriber:
    """Subscribes to the same topics Halo firmware subscribes to per the
    HALO_ONBOARDING_FIRMWARE_TRACE.md doc:

        /{device_name}/mode          — alarm mode commands
        /{device_name}/voice_led     — voice AI LED control
        /{device_name}/recovery      — recovery mode commands
        home/presence/phone          — phone proximity (keyfob trigger)
        homeassistant/...            — HA Auto-Discovery (republished by hub)

    Records every received message with timestamp + topic + payload."""

    def __init__(self, broker_host: str, broker_port: int,
                 mqtt_user: str, mqtt_pass: str, slug: str):
        self.slug = slug
        self.received: list[tuple[float, str, dict | str]] = []
        self.lock = threading.Lock()
        self.client = mqtt.Client(client_id=f"halo-sim-{slug}", clean_session=True)
        self.client.username_pw_set(mqtt_user, mqtt_pass)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.connect(broker_host, broker_port, keepalive=30)
        self.client.loop_start()

    def _on_connect(self, client, userdata, flags, rc):
        if rc != 0:
            print(f"{ERR_C}[fake-Halo MQTT] connect rc={rc}{RESET}")
            return
        topics = [
            f"/{self.slug}/mode",
            f"/{self.slug}/voice_led",
            f"/{self.slug}/recovery",
            "home/presence/phone",
            f"homeassistant/alarm_control_panel/{self.slug}/config",
            "/control_manual_alarm",
            "/control_turn_off_automation",
        ]
        for t in topics:
            client.subscribe(t, qos=1)
            print(f"{MQTT_C}[fake-Halo MQTT] subscribed {t}{RESET}")

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
        except (ValueError, UnicodeDecodeError):
            payload = msg.payload.decode(errors="replace")
        ts = time.monotonic()
        with self.lock:
            self.received.append((ts, msg.topic, payload))
        print(f"{MQTT_C}[fake-Halo MQTT] rx {msg.topic}: "
              f"{json.dumps(payload) if isinstance(payload, dict) else payload}{RESET}")

    def wait_for_topic(self, topic: str, timeout: float = 3.0,
                       since: float = None,
                       predicate=None) -> tuple[float, dict | str] | None:
        """Block up to `timeout` seconds for a message on `topic` after `since`
        (defaults to now-0.001s). If `predicate(payload)` is given, only matches
        messages where it returns True. Returns (ts, payload) or None on timeout.
        """
        if since is None:
            since = time.monotonic() - 0.001
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                for ts, t, p in self.received:
                    if t == topic and ts > since:
                        if predicate is None or predicate(p):
                            return ts, p
            time.sleep(0.05)
        return None

    def stop(self):
        self.client.loop_stop()
        self.client.disconnect()


# ---------------------------------------------------------------------------
# Dashboard sim — walks every user-facing toggle / button
# ---------------------------------------------------------------------------
class DashboardSim:
    def __init__(self, hub_ip: str, hub_secret: str, slug: str, alarm_id: int,
                 mqtt_user: str, mqtt_pass: str):
        self.hub_ip = hub_ip
        self.slug = slug
        self.alarm_id = alarm_id
        self.base = f"http://{hub_ip}"
        self.auth = base64.b64encode(f"hub:{hub_secret}".encode()).decode()
        self.subscriber = FakeHaloMqttSubscriber(
            broker_host=hub_ip,
            broker_port=1883,
            mqtt_user=mqtt_user,
            mqtt_pass=mqtt_pass,
            slug=slug,
        )
        self.results: list[dict] = []
        time.sleep(1.5)  # let subscriber bind subscriptions

    def _hdrs(self):
        return {"Authorization": f"Basic {self.auth}",
                "Content-Type": "application/json"}

    def _api(self, method: str, path: str, **kw):
        url = f"{self.base}{path}"
        return requests.request(method, url, headers=self._hdrs(), timeout=5, **kw)

    def _check(self, name: str, action_fn, expected_topic: str | None,
               payload_predicate=None, http_ok_codes=(200, 204),
               mqtt_timeout: float = 4.0):
        """Run a dashboard action, then optionally wait for an MQTT message
        on `expected_topic` whose payload satisfies `payload_predicate`."""
        print(f"\n{DIM}{'─' * 70}{RESET}")
        print(f"{BOLD}{name}{RESET}")
        t_start = time.monotonic()
        try:
            r = action_fn()
        except Exception as exc:
            print(f"{ERR_C}HTTP exception: {exc}{RESET}")
            self.results.append({"name": name, "ok": False, "reason": str(exc)})
            return False
        print(f"{APP_C}[app→hub] {r.request.method} {r.request.url} → "
              f"HTTP {r.status_code}{RESET}")
        if r.status_code not in http_ok_codes:
            print(f"  {DIM}body: {r.text[:200]}{RESET}")
            self.results.append({"name": name, "ok": False,
                                 "reason": f"HTTP {r.status_code}: {r.text[:120]}"})
            return False

        if expected_topic is None:
            self.results.append({"name": name, "ok": True,
                                 "detail": "no MQTT expected, HTTP OK"})
            print(f"{OK_C}✓ {name}: HTTP OK (no MQTT expected){RESET}")
            return True

        msg = self.subscriber.wait_for_topic(
            expected_topic, timeout=mqtt_timeout,
            since=t_start, predicate=payload_predicate,
        )
        if msg is None:
            self.results.append({
                "name": name, "ok": False,
                "reason": f"no matching MQTT on {expected_topic} within {mqtt_timeout}s",
            })
            print(f"{ERR_C}✗ {name}: no matching MQTT received on {expected_topic}{RESET}")
            return False

        ts, payload = msg
        self.results.append({"name": name, "ok": True,
                             "detail": f"MQTT {expected_topic} → {payload}"})
        print(f"{OK_C}✓ {name}: MQTT confirmed {expected_topic} → {payload}{RESET}")
        return True

    # ---- the actual dashboard buttons / toggles ----

    def _mode_topic(self):
        return f"/{self.slug}/mode"

    def _expects_mode(self, value: str):
        """Predicate: payload is a dict with mode == value."""
        return lambda p: isinstance(p, dict) and p.get("mode") == value

    def alarm_mode_away(self):
        # User-facing flow: PATCH /api/alarms/{id} sets config.alarm_mode,
        # which triggers AlarmDeviceSerializer.update → AlarmSettings →
        # HA automation → MQTT publish to /{slug}/mode for the firmware.
        return self._check(
            "Alarm mode → AWAY",
            lambda: self._api(
                "PATCH", f"/api/alarms/{self.alarm_id}",
                json={"config": {"alarm_mode": "away"}},
            ),
            expected_topic=self._mode_topic(),
            payload_predicate=self._expects_mode("away"),
        )

    def alarm_mode_night(self):
        return self._check(
            "Alarm mode → NIGHT",
            lambda: self._api(
                "PATCH", f"/api/alarms/{self.alarm_id}",
                json={"config": {"alarm_mode": "night"}},
            ),
            expected_topic=self._mode_topic(),
            payload_predicate=self._expects_mode("night"),
        )

    def alarm_mode_disarm(self):
        return self._check(
            "Alarm mode → DISARM",
            lambda: self._api(
                "PATCH", f"/api/alarms/{self.alarm_id}",
                json={"config": {"alarm_mode": "off"}},
            ),
            expected_topic=self._mode_topic(),
            payload_predicate=self._expects_mode("disarm"),
        )

    # OccupancyIllusion → AlarmSound mapping (per AlarmDeviceSerializer):
    #   people  → people_home
    #   appliances → running_appliances
    #   barking_dogs → barking_dogs
    OCC_TO_SOUND = {
        "people": "people_home",
        "appliances": "running_appliances",
        "barking_dogs": "barking_dogs",
    }

    def occupancy_illusion(self, mode: str):
        # Valid choices: off | people | appliances | barking_dogs
        if mode == "off":
            # OFF case: alarm_mode dominates. Predicate accepts disarm OR off.
            return self._check(
                "Occupancy illusion → OFF",
                lambda: self._api(
                    "PATCH", f"/api/alarms/{self.alarm_id}",
                    json={"config": {"occupancy_illusion": "off"}},
                ),
                expected_topic=self._mode_topic(),
                payload_predicate=lambda p: (
                    isinstance(p, dict) and p.get("mode") in ("disarm", "off")
                ),
            )
        expected_sound = self.OCC_TO_SOUND[mode]
        return self._check(
            f"Occupancy illusion → {mode.upper()}",
            lambda: self._api(
                "PATCH", f"/api/alarms/{self.alarm_id}",
                json={"config": {"occupancy_illusion": mode}},
            ),
            expected_topic=self._mode_topic(),
            payload_predicate=self._expects_mode(expected_sound),
        )

    def toggle_unusual_sound(self, enabled: bool):
        return self._check(
            f"Unusual sound → {'ON' if enabled else 'OFF'}",
            lambda: self._api(
                "PATCH", f"/api/alarms/{self.alarm_id}",
                json={"config": {"unusual_sound_enabled": enabled}},
            ),
            expected_topic=None,  # backend-only flag, no firmware topic
        )

    def toggle_voice_ai(self, enabled: bool):
        return self._check(
            f"Voice AI → {'ON' if enabled else 'OFF'}",
            lambda: self._api(
                "PATCH", f"/api/alarms/{self.alarm_id}",
                json={"config": {"voice_ai_enabled": enabled}},
            ),
            expected_topic=None,
        )

    def manual_alarm_on(self):
        # Valid sound choices: alarm | people_home | running_appliances | barking_dogs
        return self._check(
            "Manual alarm → ON (alarm sound)",
            lambda: self._api("POST", "/api/alarms/manual",
                              json={"state": "on", "sound": "alarm"}),
            expected_topic="/control_manual_alarm",
            payload_predicate=lambda p: isinstance(p, dict) and p.get("sound") == "alarm",
        )

    def manual_alarm_off(self):
        # OFF goes through HA automation → republishes /{slug}/mode disarm
        return self._check(
            "Manual alarm → OFF",
            lambda: self._api("POST", "/api/alarms/manual",
                              json={"state": "off"}),
            expected_topic=self._mode_topic(),
            payload_predicate=self._expects_mode("disarm"),
        )

    @staticmethod
    def _fail_assert(msg):
        raise AssertionError(msg)

    def report(self) -> bool:
        print(f"\n{BOLD}{'═' * 70}{RESET}")
        print(f"{BOLD}DASHBOARD SIM REPORT — slug={self.slug} alarm_id={self.alarm_id}{RESET}")
        print('═' * 70)
        passed = sum(1 for r in self.results if r["ok"])
        failed = len(self.results) - passed
        for r in self.results:
            mark = f"{OK_C}✓" if r["ok"] else f"{ERR_C}✗"
            print(f"  {mark} {r['name']}{RESET}")
            if r["ok"]:
                print(f"      {DIM}{r.get('detail', '')}{RESET}")
            else:
                print(f"      {ERR_C}↳ {r.get('reason', '')}{RESET}")
        print('─' * 70)

        # MQTT raw inventory — useful for debugging firmware-side expected shapes
        print(f"{DIM}\nAll {len(self.subscriber.received)} MQTT messages received "
              f"during run:{RESET}")
        for ts, t, p in self.subscriber.received:
            preview = json.dumps(p) if isinstance(p, dict) else str(p)
            print(f"  {DIM}{t}: {preview[:120]}{RESET}")
        print('─' * 70)

        if failed == 0:
            print(f"{OK_C}{BOLD}ALL {passed} DASHBOARD ACTIONS PASSED{RESET}")
        else:
            print(f"{ERR_C}{BOLD}{failed} ACTION(S) FAILED, {passed} PASSED{RESET}")
        return failed == 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--hub", default="192.168.1.161")
    p.add_argument("--hub-secret", required=True)
    p.add_argument("--slug", required=True,
                   help="Halo identity_name (must already exist as AlarmDevice)")
    p.add_argument("--alarm-id", type=int, required=True,
                   help="AlarmDevice.id (from prior onboard sim)")
    p.add_argument("--mqtt-user", default="controller",
                   help="MQTT username (default: controller)")
    p.add_argument("--mqtt-pass", required=True,
                   help="MQTT password (from .env MQTT_CONTROLLER_PASSWORD)")
    args = p.parse_args()

    sim = DashboardSim(
        hub_ip=args.hub, hub_secret=args.hub_secret,
        slug=args.slug, alarm_id=args.alarm_id,
        mqtt_user=args.mqtt_user, mqtt_pass=args.mqtt_pass,
    )

    try:
        # Walk every dashboard action
        sim.alarm_mode_away()
        sim.alarm_mode_night()
        sim.alarm_mode_disarm()
        sim.occupancy_illusion("people")
        sim.occupancy_illusion("appliances")
        sim.occupancy_illusion("barking_dogs")
        sim.occupancy_illusion("off")
        sim.toggle_unusual_sound(True)
        sim.toggle_unusual_sound(False)
        sim.toggle_voice_ai(True)
        sim.toggle_voice_ai(False)
        sim.manual_alarm_on()
        time.sleep(2)
        sim.manual_alarm_off()
    finally:
        ok = sim.report()
        sim.subscriber.stop()
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
