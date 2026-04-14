import subprocess
import re


def parse_nmcli_line(line):
    """Parse nmcli -t output, handling escaped colons in BSSID"""
    # nmcli escapes ':' inside values as '\:'
    # So we split on unescaped ':' only
    parts = re.split(r'(?<!\\):', line)
    # Unescape '\:' back to ':'
    parts = [p.replace('\\:', ':') for p in parts]
    return parts


def get_current_connection():
    result = subprocess.check_output(
        ["nmcli", "-t", "-f", "IN-USE,SSID,BSSID,FREQ", "dev", "wifi"]
    ).decode()

    for line in result.split("\n"):
        if line.startswith("*"):
            parts = parse_nmcli_line(line)
            # parts = ["*", ssid, "AA:BB:CC:DD:EE:FF", "5745 MHz"]
            if len(parts) < 4:
                continue
            ssid = parts[1]
            bssid = parts[2]
            freq_str = parts[3].replace(" MHz", "").strip()
            return {
                "ssid": ssid,
                "bssid": bssid,
                "freq": int(freq_str)
            }
    return None


def scan_wifi():
    subprocess.call(["nmcli", "dev", "wifi", "rescan"],
                    stderr=subprocess.DEVNULL)

    result = subprocess.check_output(
        ["nmcli", "-t", "-f", "SSID,BSSID,FREQ,SIGNAL", "dev", "wifi"]
    ).decode()

    networks = []
    for line in result.split("\n"):
        if not line:
            continue

        parts = parse_nmcli_line(line)
        if len(parts) < 4:
            continue

        ssid = parts[0]
        bssid = parts[1]
        freq_str = parts[2].replace(" MHz", "").strip()
        signal_str = parts[3].strip()

        try:
            networks.append({
                "ssid": ssid,
                "bssid": bssid,
                "freq": int(freq_str),
                "signal": int(signal_str)
            })
        except ValueError:
            continue

    return networks


def find_24g_network(current_bssid, networks):
    prefix = current_bssid[:8]  # So sánh 3 octet đầu "AA:BB:CC"

    candidates = [
        net for net in networks
        if net["freq"] < 3000 and net["bssid"].startswith(prefix)
    ]

    if not candidates:
        return None

    candidates.sort(key=lambda x: x["signal"], reverse=True)
    return candidates[0]["ssid"]


# def main():
#     connection = get_current_connection()

#     if not connection:
#         print("Hub is not connected to WiFi")
#         return

#     print(f"Đang kết nối: {connection['ssid']} ({connection['freq']} MHz)")

#     if connection["freq"] < 3000:
#         print(f"Đã đang dùng 2.4GHz rồi: {connection['ssid']}")
#         return

#     networks = scan_wifi()
#     ssid_24g = find_24g_network(connection["bssid"], networks)

#     if ssid_24g:
#         print(f"2.4GHz network: {ssid_24g}")
#     else:
#         print("Không tìm thấy mạng 2.4GHz cùng router")
import hashlib
import json
import os
import secrets

from celery import shared_task
from django.conf import settings


def read_json_file(path):
    """Read JSON file, return dict or empty dict if missing/invalid."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {}

def write_json_file(path, data):
    """Write dict to JSON file with indentation."""
    with open(path, "w") as f:
        json.dump(data, f, indent=4)


def main(refresh_token):
    """
    Update the RING token in the ring-state file and update MQTT in config file.
    """
    try:
        # --- Update state file ---
        state_file = "/root/jupyter-container/ring-mqtt-data/ring-state.json"
        state_data = read_json_file(state_file)
        print(f"Current state data: {state_data}")

        if not state_data.get("systemId"):
            random_bytes = secrets.token_bytes(32)
            state_data["systemId"] = hashlib.sha256(random_bytes).hexdigest()

        state_data["ring_token"] = refresh_token
        write_json_file(state_file, state_data)

        # --- Update config file ---
        
        write_json_file(config_file, config_data)
    except Exception as e:
        return f"Failed to update ring token: {e}"

if __name__ == "__main__":
    main('abc')