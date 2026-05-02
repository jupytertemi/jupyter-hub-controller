#!/usr/bin/env python3
"""Halo onboard end-to-end simulator.

Runs against a real hub. Spawns a fake Halo softAP locally + a TCP client
that registers to hub:4444 like real firmware. Walks the EXACT 8-step
app→hub→Halo dance per the v1.6 contract, asserts each step's success,
and dumps raw request/response on each transition.

Usage:
    python tools/sim/halo_onboard_sim.py \\
        --hub 192.168.1.161 \\
        --hub-secret "$(ssh root@hub 'grep ^HUB_SECRET= /root/jupyter-hub-controller/.env | cut -d= -f2')"

    # Inject failure at any step to validate error paths:
    python tools/sim/halo_onboard_sim.py --hub ... --fail audiosave

Exit code: 0 if all steps pass, 1 otherwise.
Designed to be CI-friendly + zero-hardware-required.
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import secrets
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ANSI colors for boundary identification in dense logs
RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"
APP_C = "\033[92m"   # green — app-side
HALO_C = "\033[94m"  # blue  — Halo-side (HTTP captive portal + TCP register)
HUB_C = "\033[93m"   # yellow — hub-side
ERR_C = "\033[91m"
OK_C = "\033[92m"

# ---------------------------------------------------------------------------
# Fake Halo HTTP captive portal — mimics 192.168.4.1
# ---------------------------------------------------------------------------
class FakeHaloAP:
    """HTTP server mimicking Halo firmware's captive portal endpoints.

    Per /Users/topsycombs/Downloads/HaloFirmware/.../HALO_ONBOARDING_FIRMWARE_TRACE.md:
        GET /                    → 200 status JSON
        GET /api/device_secret   → {"device_secret": "<64-hex>", "serial": "<slug>"}
        GET /audiosave?...       → stores hub config; 200 OK
        GET /wifisave?ssid=...   → stores WiFi creds; 200 OK; Halo "reboots"
        GET /api/status          → battery, fw, rssi, etc.
    """

    def __init__(self, port: int, slug: str, fail_modes: set[str]):
        self.port = port
        self.slug = slug
        self.fail_modes = fail_modes
        self.device_secret = secrets.token_hex(32)  # 64 hex chars, like firmware
        self.fw_version = "2.21.0-sim"
        self.received_audiosave = None
        self.received_wifisave = None
        self.server = None
        self.thread = None

    def _make_handler(self):
        sim = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return  # silence default access log

            def _ok_json(self, payload):
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _err(self, status, msg):
                body = json.dumps({"error": msg}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                u = urlparse(self.path)
                qs = parse_qs(u.query)

                if u.path == "/":
                    if "ping" in sim.fail_modes:
                        self.send_response(503)
                        self.end_headers()
                        return
                    self._ok_json({
                        "status": "ok",
                        "device": sim.slug,
                        "firmware": sim.fw_version,
                    })
                elif u.path == "/api/device_secret":
                    if "device_secret" in sim.fail_modes:
                        self._err(403, "blocked")
                        return
                    self._ok_json({
                        "device_secret": sim.device_secret,
                        "serial": sim.slug,
                    })
                elif u.path == "/audiosave":
                    if "audiosave" in sim.fail_modes:
                        self._err(500, "simulated_failure")
                        return
                    sim.received_audiosave = {k: v[0] for k, v in qs.items()}
                    print(f"{HALO_C}[fake-Halo HTTP] /audiosave received "
                          f"local_ip={sim.received_audiosave.get('local_ip')} "
                          f"port={sim.received_audiosave.get('port')} "
                          f"api_key={(sim.received_audiosave.get('api_key') or '')[:12]}...{RESET}")
                    self._ok_json({"status": "ok", **sim.received_audiosave})
                elif u.path == "/wifisave":
                    if "wifisave" in sim.fail_modes:
                        self._err(400, "SSID required")
                        return
                    sim.received_wifisave = {k: v[0] for k, v in qs.items()}
                    print(f"{HALO_C}[fake-Halo HTTP] /wifisave received "
                          f"ssid={sim.received_wifisave.get('ssid')} psk=<redacted>{RESET}")
                    self._ok_json({
                        "status": "ok",
                        "ssid": sim.received_wifisave.get("ssid"),
                        "device": sim.slug,
                    })
                elif u.path == "/api/status":
                    self._ok_json({
                        "device": sim.slug,
                        "firmware": sim.fw_version,
                        "battery": 96,
                        "voltage": 7.49,
                        "temperature": 27.7,
                        "wifi_rssi": -48,
                        "wifi_quality": "Excellent",
                        "uptime": 123,
                    })
                else:
                    self._err(404, f"not_found {u.path}")

        return H

    def start(self):
        self.server = HTTPServer(("127.0.0.1", self.port), self._make_handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        print(f"{HALO_C}[fake-Halo HTTP] listening on 127.0.0.1:{self.port} "
              f"(mimicking Halo's 192.168.4.1 captive portal){RESET}")

    def stop(self):
        if self.server:
            self.server.shutdown()


# ---------------------------------------------------------------------------
# Fake Halo TCP client — mimics firmware's port-4444 register
# ---------------------------------------------------------------------------
class FakeHaloTcp:
    """TCP client that opens a socket to hub:4444 and sends the register
    payload with the same JSON shape as Halo firmware does on first boot
    after WiFi join."""

    def __init__(self, hub_ip: str, slug: str, device_secret: str):
        self.hub_ip = hub_ip
        self.slug = slug
        self.device_secret = device_secret
        self.sock = None
        self.heartbeat_thread = None
        self.running = False

    def register(self) -> bool:
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(5)
            self.sock.connect((self.hub_ip, 4444))
            payload = {
                "action": "register",
                "role": "esp",
                "device": self.slug,
                "s1_count": 0,
                "k11_count": 0,
                "device_secret": self.device_secret,
            }
            self.sock.sendall((json.dumps(payload) + "\n").encode())
            print(f"{HALO_C}[fake-Halo TCP] → hub:4444 register "
                  f"device={self.slug} device_secret_len={len(self.device_secret)}{RESET}")
            self.running = True
            return True
        except Exception as exc:
            print(f"{ERR_C}[fake-Halo TCP] register failed: {exc}{RESET}")
            return False

    def stop(self):
        self.running = False
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Hub API client (the "app" side)
# ---------------------------------------------------------------------------
class HubClient:
    def __init__(self, hub_ip: str, hub_secret: str):
        self.base = f"http://{hub_ip}"
        self.auth = base64.b64encode(f"hub:{hub_secret}".encode()).decode()

    def _hdrs(self):
        return {"Authorization": f"Basic {self.auth}",
                "Content-Type": "application/json"}

    def get_onboard_payload(self, slug: str, name: str = "Sim Halo"):
        # 15s timeout — backend's wifi-freq check does an nmcli rescan
        # (~2.5s wait for scan results) on every call, so the endpoint
        # is intentionally slow on first hit
        return requests.get(
            f"{self.base}/api/halo/onboard-payload",
            params={"slug": slug, "name": name},
            headers=self._hdrs(),
            timeout=15,
        )

    def wait_online(self, slug: str, timeout: int = 30):
        return requests.get(
            f"{self.base}/api/alarms/wait-online",
            params={"identity_name": slug, "timeout": timeout},
            headers=self._hdrs(),
            timeout=timeout + 5,
        )

    def patch_alarm_name(self, alarm_id: int, name: str):
        return requests.patch(
            f"{self.base}/api/alarms/{alarm_id}",
            json={"name": name},
            headers=self._hdrs(),
            timeout=5,
        )

    def delete_alarm(self, alarm_id: int):
        return requests.delete(
            f"{self.base}/api/alarms/{alarm_id}",
            headers=self._hdrs(),
            timeout=10,
        )


# ---------------------------------------------------------------------------
# Orchestrator — walks the 8-step onboard
# ---------------------------------------------------------------------------
class OnboardSim:
    def __init__(self, hub_ip: str, hub_secret: str, slug: str,
                 name: str, fail: set[str], fake_port: int = 8014,
                 cleanup: bool = True):
        self.hub_ip = hub_ip
        self.slug = slug
        self.name = name
        self.fail = fail
        self.cleanup = cleanup
        self.fake_halo = FakeHaloAP(port=fake_port, slug=slug, fail_modes=fail)
        self.fake_tcp = None
        self.hub = HubClient(hub_ip, hub_secret)
        self.steps: list[dict] = []
        self.alarm_id = None

    def _step_start(self, n: int, desc: str) -> float:
        print(f"\n{DIM}{'─' * 70}{RESET}")
        print(f"{BOLD}STEP {n}: {desc}{RESET}")
        return time.monotonic()

    def _step_ok(self, n: int, desc: str, t0: float, detail: str = ""):
        elapsed = (time.monotonic() - t0) * 1000
        msg = f"{OK_C}✓ STEP {n} OK ({elapsed:.0f} ms): {desc}{RESET}"
        if detail:
            msg += f"\n  {DIM}{detail}{RESET}"
        print(msg)
        self.steps.append({"n": n, "desc": desc, "ok": True, "elapsed_ms": elapsed, "detail": detail})

    def _step_fail(self, n: int, desc: str, t0: float, reason: str):
        elapsed = (time.monotonic() - t0) * 1000
        print(f"{ERR_C}✗ STEP {n} FAIL ({elapsed:.0f} ms): {reason}{RESET}")
        self.steps.append({"n": n, "desc": desc, "ok": False,
                           "elapsed_ms": elapsed, "reason": reason})

    @staticmethod
    def _trunc(s, n=200):
        return s if len(s) <= n else s[:n] + "..."

    def run(self) -> bool:
        try:
            self.fake_halo.start()
            time.sleep(0.2)  # let HTTP server bind

            # ------------------------------------------------------------
            # STEP 1 — App fetches the bonding payload (marks slug pending in Redis)
            # ------------------------------------------------------------
            t0 = self._step_start(1, "App: GET /api/halo/onboard-payload (marks slug pending)")
            r = self.hub.get_onboard_payload(self.slug, self.name)
            print(f"{APP_C}[app→hub] {r.status_code}{RESET}")
            if r.status_code == 200:
                body = r.json()
                print(f"  payload: hub_ip={body['hub_ip']} mdns={body['hub_mdns']} "
                      f"audio_port={body.get('audio_port')} "
                      f"api_token={body['halo_api_token'][:12]}...")
                self.payload = body
                self._step_ok(1, "onboard-payload", t0,
                              f"slug pending in Redis; HMAC token derived")
            else:
                self._step_fail(1, "onboard-payload", t0,
                                f"HTTP {r.status_code} — {self._trunc(r.text)}")
                return False

            # ------------------------------------------------------------
            # STEP 2 — App pings the Halo's softAP (1s timeout per Phase A)
            # ------------------------------------------------------------
            t0 = self._step_start(2, "App: ping fake-Halo /  (1s timeout)")
            try:
                r = requests.get(f"http://127.0.0.1:{self.fake_halo.port}/", timeout=1)
                if r.status_code == 200:
                    self._step_ok(2, "Halo softAP alive", t0,
                                  f"firmware={r.json().get('firmware')}")
                else:
                    self._step_fail(2, "ping", t0, f"HTTP {r.status_code}")
                    return False
            except requests.RequestException as exc:
                self._step_fail(2, "ping", t0, f"unreachable: {exc}")
                return False

            # ------------------------------------------------------------
            # STEP 3 — App reads device_secret from Halo (parallel with audiosave per Phase A)
            # ------------------------------------------------------------
            t0 = self._step_start(3, "App: GET fake-Halo /api/device_secret  +  /audiosave  (parallel)")

            def _device_secret_call():
                return requests.get(f"http://127.0.0.1:{self.fake_halo.port}/api/device_secret", timeout=2)

            def _audiosave_call():
                # halo_api_token is the per-Halo HMAC; firmware stores it as api_key NVS value
                # audio_port = 5555 (firmware audio receiver port; NOT MQTT)
                return requests.get(
                    f"http://127.0.0.1:{self.fake_halo.port}/audiosave",
                    params={
                        "local_ip": self.payload["hub_ip"],
                        "port": self.payload["audio_port"],
                        "api_key": self.payload["halo_api_token"],
                        "api_path": self.payload["api_path"],
                        "hub_slug": self.payload["halo_slug"],
                    },
                    timeout=2,
                )

            t_par = time.monotonic()
            results = [None, None]
            errs = [None, None]

            def t1():
                try: results[0] = _device_secret_call()
                except Exception as e: errs[0] = e
            def t2():
                try: results[1] = _audiosave_call()
                except Exception as e: errs[1] = e

            ths = [threading.Thread(target=t1), threading.Thread(target=t2)]
            for th in ths: th.start()
            for th in ths: th.join()
            par_elapsed = (time.monotonic() - t_par) * 1000

            ds_resp, audio_resp = results
            if errs[0]:
                self._step_fail(3, "device_secret/audiosave parallel", t0, f"device_secret: {errs[0]}")
                return False
            if errs[1]:
                self._step_fail(3, "device_secret/audiosave parallel", t0, f"audiosave: {errs[1]}")
                return False
            if ds_resp.status_code != 200:
                self._step_fail(3, "device_secret", t0, f"HTTP {ds_resp.status_code}")
                return False
            if audio_resp.status_code != 200:
                self._step_fail(3, "audiosave", t0, f"HTTP {audio_resp.status_code} — {audio_resp.text[:100]}")
                return False

            self.device_secret_from_halo = ds_resp.json()["device_secret"]
            self._step_ok(3, "device_secret + audiosave succeeded in parallel", t0,
                          f"parallel-ms={par_elapsed:.0f}; device_secret={len(self.device_secret_from_halo)}-hex; "
                          f"audiosave hub_ip={self.fake_halo.received_audiosave.get('local_ip')}")

            # ------------------------------------------------------------
            # STEP 4 — App sends /wifisave; Halo "reboots" (for our purposes, just simulates delay)
            # ------------------------------------------------------------
            t0 = self._step_start(4, "App: GET fake-Halo /wifisave?ssid=...&psk=...")
            r = requests.get(
                f"http://127.0.0.1:{self.fake_halo.port}/wifisave",
                params={"ssid": self.payload["wifi_ssid"],
                        "psk": self.payload["wifi_password"]},
                timeout=2,
            )
            if r.status_code != 200:
                self._step_fail(4, "wifisave", t0, f"HTTP {r.status_code}: {r.text[:100]}")
                return False
            self._step_ok(4, "wifisave OK", t0, "Halo would now NVS-flush + reboot")

            # ------------------------------------------------------------
            # STEP 5 — Simulate Halo reboot delay (~0.5 s) + TCP-register to hub:4444
            # ------------------------------------------------------------
            t0 = self._step_start(5, "fake-Halo: reboot delay + TCP register to hub:4444")
            time.sleep(0.5)
            self.fake_tcp = FakeHaloTcp(self.hub_ip, self.slug, self.fake_halo.device_secret)
            if "register" in self.fail:
                self._step_fail(5, "register (injected fail)", t0, "skipped per --fail register")
                return False
            if not self.fake_tcp.register():
                self._step_fail(5, "register", t0, "TCP connect to hub:4444 failed")
                return False
            self._step_ok(5, "register sent over TCP", t0,
                          "hub transfer_server should fire webhook → "
                          "hub Django auto-creates AlarmDevice row")

            # ------------------------------------------------------------
            # STEP 6 — App long-polls wait-online (transfer_server webhook → Django creates row)
            # ------------------------------------------------------------
            t0 = self._step_start(6, "App: GET /api/alarms/wait-online (long-poll, timeout=20)")
            r = self.hub.wait_online(self.slug, timeout=20)
            print(f"{APP_C}[app→hub] {r.status_code}{RESET}")
            if r.status_code != 200:
                self._step_fail(6, "wait-online", t0,
                                f"HTTP {r.status_code} — {self._trunc(r.text)}")
                return False
            device = r.json()["device"]
            self.alarm_id = device["id"]
            self._step_ok(6, "AlarmDevice row appeared", t0,
                          f"id={device['id']} ip={device['ip_address']} "
                          f"identity={device['identity_name']}")

            # ------------------------------------------------------------
            # STEP 7 — App PATCH /api/alarms/{id} with the user-supplied name
            # ------------------------------------------------------------
            t0 = self._step_start(7, f"App: PATCH /api/alarms/{self.alarm_id} (set name='{self.name}')")
            r = self.hub.patch_alarm_name(self.alarm_id, self.name)
            if r.status_code in (200, 204):
                self._step_ok(7, "name saved", t0, f"HTTP {r.status_code}")
            else:
                self._step_fail(7, "patch alarm", t0,
                                f"HTTP {r.status_code} — {self._trunc(r.text)}")

            # ------------------------------------------------------------
            # STEP 8 — Verify the row is fully consistent (defense)
            # ------------------------------------------------------------
            t0 = self._step_start(8, "Verify: GET /api/alarms/wait-online again, compare")
            r = self.hub.wait_online(self.slug, timeout=2)
            if r.status_code != 200:
                self._step_fail(8, "verify", t0, f"HTTP {r.status_code}")
                return False
            v = r.json()["device"]
            if v["identity_name"] == self.slug and v["id"] == self.alarm_id:
                self._step_ok(8, "row consistent across reads", t0,
                              f"id={v['id']} name='{v['name']}' "
                              f"mac={v['mac_address']} fw='{v.get('version_fw') or '<empty — Celery enrichment runs async>'}'")
            else:
                self._step_fail(8, "verify", t0, "row diverged on second read")

            return True

        finally:
            print(f"\n{DIM}{'─' * 70}{RESET}")
            print(f"{DIM}TEARDOWN{RESET}")
            if self.fake_tcp:
                self.fake_tcp.stop()
            self.fake_halo.stop()
            if self.cleanup and self.alarm_id:
                try:
                    print(f"  cleaning up: DELETE /api/alarms/{self.alarm_id}")
                    r = self.hub.delete_alarm(self.alarm_id)
                    print(f"  cleanup HTTP {r.status_code}")
                except Exception as exc:
                    print(f"  cleanup failed (non-fatal): {exc}")

    def report(self) -> bool:
        print(f"\n{BOLD}{'═' * 70}{RESET}")
        print(f"{BOLD}SIMULATION REPORT — slug={self.slug}{RESET}")
        print('═' * 70)
        passed = sum(1 for s in self.steps if s["ok"])
        failed = len(self.steps) - passed
        for s in self.steps:
            mark = f"{OK_C}✓" if s["ok"] else f"{ERR_C}✗"
            print(f"  {mark} STEP {s['n']:>2} ({s['elapsed_ms']:>6.0f} ms): {s['desc']}{RESET}")
            if not s["ok"]:
                print(f"      {ERR_C}↳ {s['reason']}{RESET}")
        print('─' * 70)
        total_ms = sum(s["elapsed_ms"] for s in self.steps)
        if failed == 0:
            print(f"{OK_C}{BOLD}ALL {passed} STEPS PASSED in {total_ms/1000:.2f}s{RESET}")
        else:
            print(f"{ERR_C}{BOLD}{failed} STEP(S) FAILED ({passed} passed) in {total_ms/1000:.2f}s{RESET}")
        return failed == 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--hub", default="192.168.1.161", help="Hub IP")
    p.add_argument("--hub-secret", required=True, help="HUB_SECRET (from /root/jupyter-hub-controller/.env)")
    p.add_argument("--slug",
                   default=f"jupyter-alarm-sim{int(time.time()) % 100000:05d}",
                   help="Halo slug (default: time-based unique, ALL LOWERCASE because "
                        "HA automation IDs reject uppercase per Django's per-Halo "
                        "automation/script create chain)")
    p.add_argument("--name", default="Sim Halo", help="Halo display name")
    p.add_argument("--fail", action="append", default=[],
                   choices=["ping", "device_secret", "audiosave", "wifisave",
                            "register", "wait_online"],
                   help="Inject failure at this step (repeatable)")
    p.add_argument("--port", type=int, default=8014,
                   help="Local port for fake Halo HTTP server")
    p.add_argument("--no-cleanup", action="store_true",
                   help="Don't DELETE the AlarmDevice row at end")
    args = p.parse_args()

    sim = OnboardSim(
        hub_ip=args.hub,
        hub_secret=args.hub_secret,
        slug=args.slug,
        name=args.name,
        fail=set(args.fail),
        fake_port=args.port,
        cleanup=not args.no_cleanup,
    )
    sim.run()
    return 0 if sim.report() else 1


if __name__ == "__main__":
    sys.exit(main())
