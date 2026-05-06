import os
import time
import logging
import subprocess
import psycopg2
import base64
import requests
import threading

from psycopg2 import pool
from dotenv import load_dotenv
from scapy.all import ARP, Ether, srp
from jinja2 import Environment, FileSystemLoader
from dateutil.parser import parse
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
import signal
import base64


# --------------------------------------------------
# LOAD ENV
# --------------------------------------------------

ENV_FILE = "/root/jupyter-hub-controller/.env"

if os.path.exists(ENV_FILE):
    load_dotenv(ENV_FILE)


# --------------------------------------------------
# ENV VARIABLES
# --------------------------------------------------

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = os.getenv("DB_PORT", "5433")
DB_NAME = os.getenv("DB_NAME", "hub_controller")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD")

SCAN_NETWORK = os.getenv("SCAN_NETWORK", "192.168.1.0/24")
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "10"))

LOOP_TIMEOUT = int(os.getenv("LOOP_TIMEOUT", "30"))

MQTT_USER = os.getenv("MQTT_FRIGATE_USERNAME", "frigate")
MQTT_PASSWORD = os.getenv("MQTT_FRIGATE_PASSWORD")

DEVICE_NAME = os.getenv("DEVICE_NAME")
DEVICE_SECRET = os.getenv("DEVICE_SECRET")
JUPYTER_HOST = os.getenv("JUPYTER_HOST")

FRIGATE_CONTAINER = os.getenv("FRIGATE_CONTAINER_NAME", "frigate")
MEDIAMTX_SERVICE = os.getenv("MEDIAMTX_SERVICE", "mediamtx")


# --------------------------------------------------
# PATHS
# --------------------------------------------------

TEMPLATE_DIR = "/root/jupyter-hub-controller/camera/templates"
FRIGATE_CONFIG_PATH = "/root/jupyter-container/frigate/config/config.yaml"
MEDIAMTX_CONFIG_PATH = "/root/mediamtx/mediamtx.yml"
DOCKER_COMPOSE_DIR = "/root/jupyter-container"


# --------------------------------------------------
# LOGGING (FULL + FILTER FILE)
# --------------------------------------------------

LOG_DIR = "/root/ip-monitor"
os.makedirs(LOG_DIR, exist_ok=True)

log = logging.getLogger("ip-monitor")
log.setLevel(logging.DEBUG)

formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] [%(funcName)s] %(message)s"
)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
console_handler.setFormatter(formatter)


class IPChangeFilter(logging.Filter):
    def filter(self, record):
        return "[IP_CHANGE]" in record.getMessage()

file_handler = RotatingFileHandler(
    f"{LOG_DIR}/ip-monitor.log",
    maxBytes=10 * 1024 * 1024,
    backupCount=5
)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(formatter)
file_handler.addFilter(IPChangeFilter())

log.addHandler(console_handler)
log.addHandler(file_handler)


# --------------------------------------------------
# DB POOL (FIX #1)
# --------------------------------------------------

db_pool = None

def init_db_pool():
    global db_pool
    try:
        db_pool = psycopg2.pool.SimpleConnectionPool(
            1,
            10,
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            connect_timeout=10
        )
        log.info("DB pool initialized")
    except Exception as e:
        log.error(f"DB pool init failed: {e}")


def get_db_connection():
    global db_pool

    if not db_pool:
        log.error("DB pool is not initialized")
        return None

    try:
        log.debug("Connecting DB (pool)...")
        conn = db_pool.getconn()
        log.debug("DB connected")
        return conn
    except Exception as e:
        log.error(f"DB connection failed: {e}")
        return None


def release_db_connection(conn):
    global db_pool

    if not db_pool:
        log.error("DB pool is not available when releasing connection")
        return

    try:
        db_pool.putconn(conn)
    except Exception as e:
        log.error(f"Release connection failed: {e}")


# --------------------------------------------------
# SAFE SUBPROCESS (FIX #2)
# --------------------------------------------------

def safe_run(cmd, cwd=None):
    try:
        subprocess.run(
            cmd,
            cwd=cwd,
            check=True,
            timeout=60
        )
    except subprocess.TimeoutExpired:
        log.error(f"Command timeout: {cmd}")
    except subprocess.CalledProcessError as e:
        log.error(f"Command failed: {cmd} error={e}")
    except Exception as e:
        log.error(f"Command exception: {cmd} error={e}")


# --------------------------------------------------
# SAFE REQUEST (FIX #4)
# --------------------------------------------------

def safe_get(url, headers, timeout=10, retries=3):
    for i in range(retries):
        try:
            r = requests.get(
                url=url,
                headers=headers,
                timeout=timeout,
            )
            if r.status_code == 200:
                return r
            log.warning(f"Request failed {r.status_code} retry {i+1}")
        except Exception as e:
            log.warning(f"Request error {e} retry {i+1}")
        time.sleep(2 ** i)
    return None


# --------------------------------------------------
# TEMPLATE ENGINE
# --------------------------------------------------

env = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=False
)


# --------------------------------------------------
# NETWORK DETECTION
# --------------------------------------------------

def get_scan_network():
    try:
        result = subprocess.check_output(["hostname", "-I"]).decode().strip()
        ips = result.split()

        log.debug(f"Detected IPs: {ips}")

        for ip in ips:
            if ip.startswith("192.168."):
                parts = ip.split(".")
                network = f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"
                log.info(f"Using detected network {network}")
                return network

        log.warning("No 192.168.* IP detected, fallback to SCAN_NETWORK")
        return SCAN_NETWORK

    except Exception as e:
        log.error(f"Network detect failed: {e}")
        return SCAN_NETWORK


# --------------------------------------------------
# DATABASE
# --------------------------------------------------

def wait_for_db_and_prepare():
    log.info("Waiting for DB + schema ready...")

    REQUIRED_COLUMNS = {"ip", "mac_address"}  # ✅ FIX: định nghĩa rõ required columns

    while True:
        conn = None

        try:
            conn = get_db_connection()

            if not conn:
                time.sleep(2)
                continue

            with conn.cursor() as cur:

                # ✅ FIX: check table tồn tại
                cur.execute("SELECT to_regclass('public.camera_camera')")
                table = cur.fetchone()[0]

                if not table:
                    log.warning("Table camera_camera not created yet...")
                    time.sleep(2)
                    continue

                # ✅ FIX: lấy danh sách column
                cur.execute("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'camera_camera'
                """)

                columns = {row[0] for row in cur.fetchall()}

                # ✅ FIX: đảm bảo column ip tồn tại
                if "ip" not in columns:
                    log.warning("Column 'ip' not ready yet...")
                    time.sleep(2)
                    continue

                # ✅ FIX QUAN TRỌNG: dùng IF NOT EXISTS (tránh race condition)
                if "mac_address" not in columns:
                    log.warning("Column 'mac_address' missing, adding...")
                    try:
                        cur.execute("""
                            ALTER TABLE camera_camera
                            ADD COLUMN IF NOT EXISTS mac_address VARCHAR(32)
                        """)
                        conn.commit()
                        log.info("Column mac_address ensured")
                        continue  # 🔥 FIX: loop lại để re-check schema
                    except Exception as e:
                        log.error(f"Add column failed: {e}")
                        time.sleep(2)
                        continue

                # ✅ FIX: chỉ return khi ĐỦ column
                if not REQUIRED_COLUMNS.issubset(columns):
                    log.warning(f"Columns not ready: {REQUIRED_COLUMNS - columns}")
                    time.sleep(2)
                    continue

                log.info("DB + schema ready ✅")
                return

        except Exception as e:
            log.warning(f"DB not ready: {e}")

        finally:
            if conn:
                release_db_connection(conn)

        time.sleep(5)


def update_camera_mac(cam, mac, ip):
    conn = get_db_connection()

    if not conn:
        return False

    try:
        with conn.cursor() as cur:

            cur.execute(
                """
                UPDATE camera_camera
                SET mac_address=%s, ip=%s
                WHERE id=%s
                """,
                (mac, ip, cam["id"])
            )

            conn.commit()

            log.info(
                f"Learned MAC Camera={cam['name']} mac={mac} ip={ip}"
            )

            return True

    finally:
        release_db_connection(conn)


# --------------------------------------------------
# EXTRACT IP FROM RTSP
# --------------------------------------------------

def extract_ip_from_rtsp(rtsp):
    try:
        host = rtsp.rsplit("@", 1)[1]
        ip = host.split("/")[0].split(":")[0]
        return ip
    except Exception:
        return None


# --------------------------------------------------
# CAMERA LIST
# --------------------------------------------------

def get_camera_list():
    conn = get_db_connection()
    if not conn:
        return []

    try:
        with conn.cursor() as cur:

            try:
                cur.execute("""
                    SELECT id, name, mac_address, ip, rtsp_url
                    FROM camera_camera
                    WHERE rtsp_url IS NOT NULL
                """)

            except psycopg2.errors.UndefinedColumn:
                log.warning("mac_address missing → attempting self-heal")

                conn.rollback()  # 🔥 FIX BẮT BUỘC

                try:
                    # 🔥 FIX: dùng IF NOT EXISTS tránh race
                    cur.execute("""
                        ALTER TABLE camera_camera
                        ADD COLUMN IF NOT EXISTS mac_address VARCHAR(32)
                    """)
                    conn.commit()

                    log.info("mac_address column added (runtime self-heal)")

                except Exception as e:
                    log.error(f"Failed to add mac_address: {e}")
                    conn.rollback()

                # 🔥 FIX: fallback query để không crash
                cur.execute("""
                    SELECT id, name, NULL as mac_address, ip, rtsp_url
                    FROM camera_camera
                    WHERE rtsp_url IS NOT NULL
                """)

            rows = cur.fetchall()

            cameras = []
            for row in rows:
                cameras.append({
                    "id": row[0],
                    "name": row[1],
                    "mac": row[2].lower() if row[2] else None,
                    "ip": row[3],
                    "rtsp": row[4]
                })

            return cameras

    finally:
        release_db_connection(conn)


def get_cameras_for_config():
    conn = get_db_connection()
    if not conn:
        return []

    try:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT slug_name, rtsp_url
                FROM camera_camera
                WHERE rtsp_url IS NOT NULL
                ORDER BY id
            """)

            cameras = []

            for row in cur.fetchall():
                new_rtsp_url = row[1].replace("ring-mqtt", "localhost")
                cameras.append({
                    "name": row[0],
                    "rtsp_url": new_rtsp_url,
                    # "mediamtx_rtsp_url": f"rtsp://host.docker.internal:8556/{row[0]}",
                    "zones": []
                })

            return cameras

    finally:
        release_db_connection(conn)


# --------------------------------------------------
# TURN SERVER
# --------------------------------------------------

def generate_basic_token(username, password):
    token = f"{username}:{password}"
    return "Basic " + base64.b64encode(token.encode()).decode()


def get_serial_number():
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("Serial"):
                    return line.split(":")[1].strip()
    except Exception:
        pass

    return "unknown"


def revoke_turns_credential():
    try:
        serial = get_serial_number()

        headers = {
            "Content-type": "application/json",
            "Authorization": generate_basic_token(
                DEVICE_NAME,
                DEVICE_SECRET
            ),
        }

        url = f"{JUPYTER_HOST}/turns-credentials/{serial}/revoke"

        log.info(f"Request TURN credential serial={serial}")

        r = safe_get(url, headers)

        if not r:
            log.error("TURN credential failed after retries")
            return {}

        return r.json()

    except Exception as e:
        log.error(f"TURN credential error {e}")
        return {}


# 2026-05-07: 403 backoff + IP-change detection. Pre-fix: 1 PATCH per 10s
# regardless of IP change; on cloud-auth 403 a hub burned ~7,500 log lines/h.
# Now: only call when local_ip changes OR every 1h for re-verify; on >=5
# consecutive 403s, throttle retries to every 5 min.
_last_set_host_403_at = 0
_consecutive_403s = 0
_last_success_ip = None
_last_success_at = 0
_BACKOFF_AFTER_N_403S = 5
_BACKOFF_INTERVAL_S = 300
_REVERIFY_INTERVAL_S = 3600


def set_hub_host():
    global _last_set_host_403_at, _consecutive_403s, _last_success_ip, _last_success_at
    try:
        local_ip = subprocess.getoutput("hostname -I").split()[0]
        if not local_ip:
            log.error("Not found ip host")
            return {}, False
        now = time.time()
        # Skip if IP hasnt changed and we re-verified recently (1h)
        if _last_success_ip == local_ip and (now - _last_success_at) < _REVERIFY_INTERVAL_S:
            return {}, True
        # Backoff during 403 streak — at most 1 retry per 5 min
        if _consecutive_403s >= _BACKOFF_AFTER_N_403S and (now - _last_set_host_403_at) < _BACKOFF_INTERVAL_S:
            return {}, False
        serial = get_serial_number()
        headers = {
            "Content-type": "application/json",
            "Authorization": generate_basic_token(DEVICE_NAME, DEVICE_SECRET),
        }
        url = f"{JUPYTER_HOST}/hub/host"
        payload = {"local_host": str(local_ip)}
        log.info(f"Request set hub host serial={serial} to {local_ip}")
        r = requests.patch(url=url, headers=headers, json=payload, timeout=10)
        if r.status_code == 200:
            log.info("Set host request status 200")
            _last_success_ip = local_ip
            _last_success_at = now
            _consecutive_403s = 0
            return r.json(), True
        elif r.status_code == 403:
            _consecutive_403s += 1
            _last_set_host_403_at = now
            if _consecutive_403s == 1:
                log.error(f"Set host HTTP 403 (cloud auth issue) — will back off after {_BACKOFF_AFTER_N_403S} consecutive")
            elif _consecutive_403s == _BACKOFF_AFTER_N_403S:
                log.error(f"Set host 403 streak hit {_BACKOFF_AFTER_N_403S} — throttling to 1 retry per {_BACKOFF_INTERVAL_S}s")
            else:
                log.warning(f"Set host 403 (#{_consecutive_403s} consecutive)")
            return {}, False
        log.error(f"Set host fail HTTP {r.status_code}")
        return {}, False

    except requests.exceptions.ConnectionError as e:
        log.error(f"Error executing set host: {e}")
        return {}, False

    except Exception as e:
        log.error(f"Error executing set host: {e}")
        return {}, False


def parse_ice_server():
    try:
        ice_response = revoke_turns_credential()
        
        log.info(f"ICE RESPONSE: {ice_response}")

        if ice_response == {}:
            return None

        ice_server = ice_response.get("previous_turn")

        if not ice_server:
            ice_server = ice_response
        else:
            created_at_str = ice_server.get("created_at")

            if not created_at_str:
                return None

            created_at = parse(created_at_str)

            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)

            expire_time = created_at + timedelta(days=2)
            now = datetime.now(timezone.utc)

            remaining_time = expire_time - now

            if remaining_time < timedelta(hours=24):
                log.info("Using new TURN credential")
                ice_server = ice_response

        ice_credential = ice_server.get("credential")

        if not ice_credential or len(ice_credential) < 2:
            log.error("Invalid ICE credential")
            return None

        return {
            "stun_server": ice_credential[0]["urls"],
            "turn_server": ice_credential[1]["urls"],
            "turn_user": ice_credential[1]["username"],
            "turn_password": ice_credential[1]["credential"],
        }

    except Exception as e:
        log.error(f"parse ICE error: {e}")
        return None


# --------------------------------------------------
# TEMPLATE RENDER
# --------------------------------------------------

def render_and_write_config(template_name, context, output_path):
    template = env.get_template(template_name)
    config = template.render(context)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(config)

    log.info(f"Rendered config {output_path}")


# --------------------------------------------------
# CONFIG UPDATE
# --------------------------------------------------

def update_frigate_config():
    cameras = get_cameras_for_config()

    context = {
        "cameras": cameras,
        "mqtt_user": MQTT_USER,
        "mqtt_password": MQTT_PASSWORD,
    }

    render_and_write_config(
        "frigate_config.yaml",
        context,
        FRIGATE_CONFIG_PATH,
    )

    log.info("Restarting Frigate")

    safe_run(
        ["docker", "compose", "down", FRIGATE_CONTAINER],
        cwd=DOCKER_COMPOSE_DIR,
    )

    safe_run(
        ["docker", "compose", "up", "-d", FRIGATE_CONTAINER],
        cwd=DOCKER_COMPOSE_DIR,
    )


def update_mediamtx_config():
    cameras = get_cameras_for_config()

    ice = parse_ice_server()

    if not ice:
        ice = {
            "stun_server": "",
            "turn_server": "",
            "turn_user": "",
            "turn_password": ""
        }

    context = {
        "cameras": cameras,
        **ice
    }

    render_and_write_config(
        "mediamtx.yml",
        context,
        MEDIAMTX_CONFIG_PATH,
    )

    safe_run(
        ["systemctl", "restart", MEDIAMTX_SERVICE],
    )


def update_camera_config():
    log.info("Updating camera configs")

    update_mediamtx_config()
    update_frigate_config()


# --------------------------------------------------
# NETWORK SCAN
# --------------------------------------------------

def arp_scan():
    try:
        network = get_scan_network()

        log.info(f"Scanning network {network}")

        arp = ARP(pdst=network)
        ether = Ether(dst="ff:ff:ff:ff:ff:ff")

        packet = ether / arp

        result = srp(packet, timeout=2, retry=1, verbose=0)[0]

        devices = {}

        for sent, received in result:
            devices[received.hwsrc.lower()] = received.psrc

        log.info(f"Scan found {len(devices)} devices")

        return devices
    except Exception as e:
        log.error(f"ARP scan failed: {e}")
        return {}


# --------------------------------------------------
# UPDATE CAMERA IP
# --------------------------------------------------

def update_camera_ip(cam, new_ip):
    conn = get_db_connection()

    if not conn:
        return False

    try:
        with conn.cursor() as cur:

            old_ip = cam["ip"]
            new_rtsp = cam["rtsp"].replace(old_ip, new_ip)

            cur.execute(
                """
                UPDATE camera_camera
                SET ip=%s, rtsp_url=%s
                WHERE id=%s
                """,
                (new_ip, new_rtsp, cam["id"])
            )

            conn.commit()

            log.warning(
                f"[IP_CHANGE] Camera={cam['name']} {old_ip} -> {new_ip}"
            )

            return True

    finally:
        release_db_connection(conn)


# --------------------------------------------------
# DETECT CHANGE
# --------------------------------------------------

def detect_camera_changes():
    
    try:
        set_hub_host()
    except Exception as e:
        log.error(f"Set ip host fail: {e}")
    
    cameras = get_camera_list()

    scanned_devices = arp_scan()

    config_need_update = False

    for cam in cameras:

        log.debug(f"Checking camera {cam['name']} mac={cam['mac']} ip={cam['ip']}")

        mac = cam["mac"]
        ip = cam["ip"]
        rtsp = cam["rtsp"]

        if mac:

            if mac in scanned_devices:

                new_ip = scanned_devices[mac]

                if ip != new_ip:

                    if update_camera_ip(cam, new_ip):
                        config_need_update = True

            else:
                log.debug(f"Camera {cam['name']} mac not found in scan")

        else:

            rtsp_ip = extract_ip_from_rtsp(rtsp)
            
            log.debug(
                f"Camera {cam['name']} RTSP='{rtsp}' -> extracted_ip={rtsp_ip}"
            )

            if not rtsp_ip:
                log.debug(f"Cannot extract IP from RTSP {cam['name']}")
                continue

            for scanned_mac, scanned_ip in scanned_devices.items():

                if scanned_ip == rtsp_ip:

                    log.info(
                        f"Bootstrap MAC for {cam['name']} mac={scanned_mac} ip={scanned_ip}"
                    )

                    if update_camera_mac(cam, scanned_mac, scanned_ip):
                        config_need_update = True

                    break

    if config_need_update:

        log.info("Changes detected -> updating configs")

        update_camera_config()

    else:

        log.info("No change detected")


# --------------------------------------------------
# LOOP TIMEOUT
# --------------------------------------------------

def run_with_timeout(func, timeout):

    def handler(signum, frame):
        raise TimeoutError()

    signal.signal(signal.SIGALRM, handler)
    signal.alarm(timeout)

    try:
        func()
    except TimeoutError:
        log.error("Loop execution timeout!")
    except Exception as e:
        log.error(f"Monitor error {e}")
    finally:
        signal.alarm(0)


# --------------------------------------------------
# MAIN LOOP
# --------------------------------------------------

def main():
    log.info("Camera IP monitor started")
    
    init_db_pool()
    wait_for_db_and_prepare()

    while True:

        start_time = time.time()

        try:

            run_with_timeout(detect_camera_changes, LOOP_TIMEOUT)

        except Exception as e:

            log.error(f"Monitor error {e}")

        elapsed = time.time() - start_time
        sleep_time = max(0, SCAN_INTERVAL - elapsed)

        time.sleep(sleep_time)


if __name__ == "__main__":
    main()