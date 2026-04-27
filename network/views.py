import json
import subprocess
import re

from django.conf import settings
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from rest_framework import status

from network.serializers import (
    WifiCredentialsSerializer,
    WifiNetworkSerializer,
    WifiConnectSerializer
)

from .services import WifiService


################## CHANH ##################
class WiFiNetwork:
    def __init__(self, ssid, bssid, in_use=False, freq=0, signal=0):
        self.ssid = ssid
        self.bssid = bssid.upper()
        self.in_use = in_use
        self.freq = freq
        self.signal = signal

    @property
    def oui(self):
        parts = self.bssid.split(":")
        return ":".join(parts[:3]).upper() if len(parts) >= 3 else None

    def __repr__(self):
        return f"<WiFiNetwork ssid={self.ssid} bssid={self.bssid} in_use={self.in_use}>"

class WiFiScanner:
    @staticmethod
    def run_command(cmd):
        try:
            output = subprocess.check_output(cmd, text=True)
            return output.strip().split("\n")
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] Lỗi chạy lệnh {cmd}: {e}")
            return []

    @staticmethod
    def clean_int(s):
        s_clean = re.sub(r"[^0-9\-]", "", s)
        return int(s_clean) if s_clean else 0

    def get_current_ssid(self):
        lines = self.run_command(["nmcli", "device", "wifi", "show"])
        ssid = None
        for line in lines:
            line = line.strip()
            print(f"[DEBUG] Wi-Fi current line: {line}")
            if line.startswith("SSID:"):
                ssid = line.split("SSID:")[1].strip()
        return ssid

    def scan_wifi_list(self):
        print("[INFO] Start scan Wi-Fi process.")
        wifi_list = []
        lines = self.run_command(["nmcli", "-f", "IN-USE,BSSID,SSID", "dev", "wifi", "list"])
        print("[DEBUG] Wi-Fi scan raw output:")
        
        for line in lines[1:]:  # Bỏ header
            line = line.strip()
            if not line:
                continue

            bssid_match = re.search(r"([0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2})", line)
            if not bssid_match:
                continue
            bssid = bssid_match.group(1).upper()

            in_use = line.startswith("*")
            ssid = line.split(bssid)[-1].strip()
            if ssid == "--" or not ssid:
                continue

            wifi_list.append(WiFiNetwork(ssid=ssid, bssid=bssid, in_use=in_use))
        return wifi_list

class WiFiManager:
    def __init__(self):
        self.scanner = WiFiScanner()

    def find_2g_same_network(self):
        print("[INFO] Start check Wi-Fi process.")
        current_ssid = self.scanner.get_current_ssid()
        if not current_ssid:
            print("[ERROR] Không tìm thấy SSID hiện tại.")
            return []

        wifi_list = self.scanner.scan_wifi_list()
        if not wifi_list:
            print("[ERROR] Không scan được Wi-Fi nào.")
            return []

        current_bssid = next((w.bssid for w in wifi_list if w.in_use and w.ssid == current_ssid), None)
        if not current_bssid:
            print("[ERROR] Không tìm thấy BSSID của Wi-Fi hiện tại.")
            return []

        print(f"[INFO] Wi-Fi hiện tại: {current_ssid} (BSSID: {current_bssid})")
        current_oui = WiFiNetwork(ssid=current_ssid, bssid=current_bssid).oui
        print(f"[DEBUG] OUI hiện tại: {current_oui}")

        candidates = []
        for wifi in wifi_list:
            if wifi.freq >= 3000 or wifi.in_use:
                continue

            candidate_oui = wifi.oui
            reason = ""
            if not candidate_oui:
                reason = "Rejected: không lấy được OUI"
            elif candidate_oui != current_oui:
                reason = f"Rejected: OUI '{candidate_oui}' != '{current_oui}'"
            else:
                reason = "Accepted"
                candidates.append(WiFiNetwork(ssid=wifi.ssid, bssid=wifi.bssid))

            print(f"[DEBUG] Candidate: SSID='{wifi.ssid}', BSSID={wifi.bssid}, FREQ={wifi.freq} MHz, Signal={wifi.signal}, Reason={reason}")

        if not candidates:
            print("[INFO] Không tìm thấy Wi-Fi 2.4GHz cùng mạng với Wi-Fi hiện tại.")
        else:
            print("[INFO] Các Wi-Fi 2.4GHz khả năng cao cùng mạng (không bao gồm Wi-Fi hiện tại và SSID '--'):")
            for wifi in candidates:
                print(f"- {wifi.ssid} (BSSID: {wifi.bssid})")

        return candidates
################## CHANH ##################

class GetWifiCredentialsView(APIView):
    def get(self, request):
        with open(settings.WIFI_CREDENTIALS_PATH, encoding="utf-8") as f:
            credentials = json.load(f)

        # Return hub LAN IP for Halo MQTT broker address.
        # mDNS hostname is unreliable (requires Avahi + correct system hostname).
        # LAN IP is used by the Halo for MQTT, audio streamer, and telemetry.
        import socket as _socket
        try:
            _s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
            _s.connect(("8.8.8.8", 80))
            hub_ip = _s.getsockname()[0]
            _s.close()
        except Exception:
            hub_ip = None

        # Fallback to mDNS hostname if IP detection fails
        if not hub_ip:
            try:
                with open('/etc/radxa-hostname', 'r') as f:
                    radxa_hostname = f.read().strip()
                hub_ip = f"{radxa_hostname}.local"
            except FileNotFoundError:
                hub_ip = None

        # Return current hub WiFi (already 2.4GHz) - NOT 5GHz candidates
        # ESP32 can only connect to 2.4GHz networks
        try:
            current_ssid = credentials.get("ssid", "default_ssid")
            print("[RESULT] Current hub WiFi:", current_ssid)
        except Exception as e:
            print("[ERROR] Failed to get current SSID:", e)
            current_ssid = "default_ssid"

        serializer = WifiCredentialsSerializer(
            {
                "ssid": current_ssid,
                "password": credentials.get("password", "default_password"),
                "mdns": hub_ip,
            }
        )

        return Response(serializer.data)

################## CHANH ##################

class WifiScanView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        try:
            networks = WifiService.scan_wifi()
            serializer = WifiNetworkSerializer(networks, many=True)

            return Response({
                "success": True,
                "networks": serializer.data
            })

        except Exception as e:
            return Response({
                "success": False,
                "message": str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class WifiConnectView(APIView):

    def post(self, request):
        serializer = WifiConnectSerializer(data=request.data)

        if not serializer.is_valid():
            return Response({
                "success": False,
                "errors": serializer.errors,
                "hub_local_ip": WifiService.get_local_ip()
            }, status=status.HTTP_400_BAD_REQUEST)

        ssid = serializer.validated_data["ssid"]
        password = serializer.validated_data.get("password")

        result = WifiService.connect_wifi(ssid, password)
        ip_local = WifiService.get_local_ip()

        if result["success"]:
            return Response({
                "success": True,
                "message": "Connected successfully",
                "hub_local_ip": ip_local
            })

        else:
            return Response({
                "success": False,
                "message": result.get("error", "Connect failed"),
                "hub_local_ip": ip_local
            }, status=status.HTTP_400_BAD_REQUEST)
class WifiStatusView(APIView):
    permission_classes = [AllowAny]
    
    def get(self, request):
        wifi_info = WifiService.get_current_wifi_with_signal()
        local_ip = WifiService.get_local_ip()
        
        if wifi_info:
            return Response({
                'success': True,
                'ssid': wifi_info['ssid'],
                'signal_percent': wifi_info['signal_percent'],
                'signal_dbm': wifi_info['signal_dbm'],
                'local_ip': local_ip if local_ip else ''
            })
        else:
            return Response({
                'success': True,
                'ssid': '',
                'signal_percent': 0,
                'signal_dbm': -100,
                'local_ip': local_ip if local_ip else ''
            })
