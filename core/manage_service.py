import logging
import re
import subprocess

logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
)


def get_wifi_signal_strength_and_ssid():
    try:
        # Chạy lệnh iwconfig
        output = subprocess.check_output(
            ["iwconfig"], stderr=subprocess.DEVNULL
        ).decode()

        # Tìm dòng chứa "Signal level"
        match_signal = re.search(r"Signal level=([-0-9]+) dBm", output)
        signal_level = int(match_signal.group(1)) if match_signal else None

        # Tìm dòng chứa ESSID
        match_ssid = re.search(r'ESSID:"([^"]+)"', output)
        ssid = match_ssid.group(1) if match_ssid else None

        return signal_level, ssid

    except Exception as e:
        logging.info(f"Error when obtaining WiFi strength and SSID: {e}")
        return 0, "Invalid"


def get_service_status(service_name):
    try:
        result = subprocess.run(
            ["sudo", "systemctl", "status", service_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        service_status = result.stdout.strip()

        if "active (running)" in service_status:
            return "running"
        elif "active (exited)" in service_status:
            return "exited"
        elif "inactive (dead)" in service_status:
            return "dead"
        elif "failed" in service_status:
            return "fail"
        elif (
            "reloading" in service_status
            or "activating" in service_status
            or "deactivating" in service_status
        ):
            return "processing"
        return "unhandle"
    except subprocess.CalledProcessError as e:
        logging.error(f"An error occurred: {e}")
        return "unhandle"
    except Exception as ex:
        logging.error(f"An error occurred: {ex}")
        return "unhandle"


def control_service(action, service_name):
    try:
        command = ["systemctl", action, service_name]

        subprocess.run(command, check=True)
        logging.info(f"Service '{service_name}' has been {action} successfully.")
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to enable service '{service_name}'. Error: {e}")
    except Exception as ex:
        logging.error(f"An unexpected error occurred: {ex}")
