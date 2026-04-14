import subprocess
import json

class WifiService:
    
    @staticmethod
    def is_ssid_available(target_ssid):
        networks = WifiService.scan_wifi()
        ssid_list = [n["ssid"] for n in networks]

        return target_ssid in ssid_list
    
    @staticmethod
    def get_local_ip():
        try:
            result = subprocess.check_output(
                ["hostname", "-I"],
                encoding="utf-8"
            ).strip()

            # hostname -I có thể trả nhiều IP → lấy IP đầu tiên
            return result.split()[0] if result else None

        except Exception:
            return None

    @staticmethod
    def scan_wifi():
        """
        Scan wifi using nmcli
        Return list of dict: ssid, signal, security
        """
        try:
            result = subprocess.check_output(
                ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi"],
                encoding="utf-8"
            )

            networks = []
            for line in result.strip().split("\n"):
                if not line:
                    continue

                parts = line.split(":")
                ssid = parts[0]
                
                if not ssid or ssid == "--":
                    continue
                
                signal = int(parts[1]) if parts[1].isdigit() else 0
                security = parts[2]

                networks.append({
                    "ssid": ssid,
                    "signal": signal,
                    "security": security
                })

            return networks

        except Exception as e:
            raise Exception(f"Scan wifi failed: {str(e)}")

    @staticmethod
    def get_current_wifi():
        try:
            result = subprocess.check_output(
                ["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"],
                encoding="utf-8"
            )

            for line in result.strip().split("\n"):
                if line.startswith("yes:"):
                    return line.split(":")[1]

            return None

        except Exception:
            return None

    @staticmethod
    def connect_wifi(ssid, password=None):
        """
        - chỉ connect nếu SSID có trong scan
        - nếu fail → reconnect wifi cũ
        - nếu success → xoá wifi cũ
        """

        # ❌ SSID không tồn tại
        if not WifiService.is_ssid_available(ssid):
            return {
                "success": False,
                "error": "SSID not found"
            }

        current_wifi = WifiService.get_current_wifi()

        try:
            # connect new wifi
            if password:
                subprocess.check_call(
                    ["nmcli", "dev", "wifi", "connect", ssid, "password", password]
                )
            else:
                subprocess.check_call(
                    ["nmcli", "dev", "wifi", "connect", ssid]
                )

            # success → remove old
            if current_wifi and current_wifi != ssid:
                subprocess.call(
                    ["nmcli", "connection", "delete", current_wifi]
                )
                
            wifi_credential = {
                "ssid": ssid,
                "password": password if password else "",
            }

            # Save to a JSON file
            try:
                WIFI_CREDENTIAL_FILE = "/root/jupyter-container/credentials/hub_credentials.json"
                with open(WIFI_CREDENTIAL_FILE, "w") as json_file:
                    local_ip = subprocess.getoutput("hostname -I").split()[0]
                    if local_ip is not None:
                        wifi_credential["local_ip"] = local_ip
                    json.dump(wifi_credential, json_file, indent=4)
            except Exception as e:
                print("error write wifi credential")

            return {
                "success": True
            }

        except subprocess.CalledProcessError:
            # fail → rollback
            if current_wifi:
                subprocess.call(
                    ["nmcli", "connection", "up", current_wifi]
                )

            return {
                "success": False,
                "error": "Connect failed"
            }
    @staticmethod
    def get_current_wifi_with_signal():
        try:
            result = subprocess.check_output(
                ["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL", "dev", "wifi"],
                encoding="utf-8"
            )

            for line in result.strip().split("\n"):
                if line.startswith("yes:"):
                    parts = line.split(":")
                    ssid = parts[1] if len(parts) > 1 else None
                    signal = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
                    # Convert % to dBm approximation: -100 + (signal * 0.5)
                    # Or keep as percentage - let's return both
                    return {
                        "ssid": ssid,
                        "signal_percent": signal,
                        "signal_dbm": -100 + int(signal * 0.5)
                    }

            return None

        except Exception:
            return None
