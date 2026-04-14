"""
Production settings
"""

import os  # noqa: F401

from .common import *  # noqa

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/3.2/howto/deployment/checklist/

DEBUG = os.getenv("ENV", default="dev") == "dev"  # noqa

SECRET_KEY = os.getenv(  # noqa
    "SECRET_KEY", "django-insecure-*$0b8ibx7uzk45cm+fxw7*jj(yzi2ye!l4+!dnyxa-u-nbuz=q"
)

ALLOWED_HOSTS = [os.getenv("ALLOWED_HOSTS", "*")]  # noqa

HOST = os.getenv("HOST", "http://localhost:8000/")  # noqa

# Database
# https://docs.djangoproject.com/en/3.2/ref/settings/#databases

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql_psycopg2",
        "NAME": os.getenv("DB_NAME", "hub_controller"),  # noqa
        "USER": os.getenv("DB_USERNAME", "postgres"),  # noqa
        "PASSWORD": os.getenv("DB_PASSWORD", "postgres"),  # noqa
        "HOST": os.getenv("DB_HOST", "localhost"),  # noqa
        "PORT": os.getenv("DB_PORT", "5432"),  # noqa
    }
}

# CORS config
CORS_ALLOWED_ORIGINS = os.getenv(  # noqa
    "CORS_ALLOWED_ORIGINS", "http://localhost:3000"
).split(",")

FOLDER_ROOT_PATH = os.getenv(  # noqa
    "FOLDER_ROOT_PATH",
    os.getenv("forlder_root_path", "/root/jupyter-container"),  # noqa
)

# Frigate
FRIGATE_CONFIG_PATH = f"{FOLDER_ROOT_PATH}/frigate/config/config.yaml"
RTSP_PASSWORD = os.getenv("RTSP_PASSWORD", "example_password")  # noqa
RING_CONVERTER_CONFIG_PATH = "../jupyter-ring-camera-converter/cameras.json"
FRIGATE_CONTAINER_NAME = os.getenv("FRIGATE_CONTAINER_NAME", "frigate")  # noqa
FRIGATE_MQTT_TOPIC = os.getenv("FRIGATE_MQTT_TOPIC", "frigate/events")  # noqa

FRIGATE_SERVER_ADDRESS = os.getenv(  # noqa
    "FRIGATE_SERVER_ADDRESS", f"http://{FRIGATE_CONTAINER_NAME}:5000"
)

CELERY_BROKER_URL = os.getenv("BROKER_URL", "redis://localhost:6379/0")  # noqa
CELERY_RESULT_BACKEND = os.getenv("BROKER_URL", "redis://localhost:6379/0")  # noqa

# WebRTC file trasfering
WEBRTC_FILE_SENDER_PATH = (
        Path(os.getcwd()).parent / "webrtc-file-sender/src/sender.py"  # noqa
)
WEBRTC_FILE_RECEIVER_PATH = (
        Path(os.getcwd()).parent / "webrtc-file-sender/src/receiver.py"  # noqa
)
SENDING_FILE_DIR = os.getenv(  # noqa
    "SENDING_FILE_DIR",
    Path(os.getcwd()).parent / "frigate/storage/recordings",  # noqa
)
RECEIVING_FILE_DIR = os.getenv(  # noqa
    "RECEIVING_FILE_DIR",
    Path(os.getcwd()).parent / "upload",  # noqa
)

# WebRTC play back video
WEBRTC_PLAY_BACK_VIDEO_PATH = (
        Path(os.getcwd()).parent / "webrtc-file-sender/src/video_stream.py"  # noqa
)
# MQTT
MQTT_HOST = os.getenv("MQTT_HOST", "localhost")  # noqa
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))  # noqa
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "controller")  # noqa
MQTT_PASSWORD = os.getenv("MQTT_CONTROLLER_PASSWORD", "example_password")  # noqa
MQTT_FRIGATE_USERNAME = os.getenv("MQTT_FRIGATE_USERNAME", "frigate")  # noqa
MQTT_FRIGATE_PASSWORD = os.getenv("MQTT_FRIGATE_PASSWORD", "example_password")  # noqa

# Loitering
LOITERING_CONFIG_PATH = "/root/jupyter-container/loiterai/config.py"
LOITERING_CONTAINER_NAME = os.getenv(
    "LOITERING_CONTAINER_NAME", "loiter_detection"
)

# Parcel detect
PARCEL_CONFIG_PATH = f"{FOLDER_ROOT_PATH}/pilot_parcel_theft_AI/constants.py"
PARCEL_BOX_CONFIG_PATH = os.getenv(  # noqa
    "PARCEL_BOX_CONFIG_PATH",
    Path(os.getcwd()).parent  # noqa
    / "pilot_parcel_theft_AI/bounding_box.json",  # noqa
)
PARCEL_CONTAINER_NAME = os.getenv("PARCEL_CONTAINER_NAME", "parcel_detection")  # noqa

# facial
FACIAL_CONTAINER_NAME = os.getenv("FACIAL_CONTAINER_NAME", "face_recognition")  # noqa

FACIAL_CONFIG_PATH = "/root/jupyter-container/pilot_face_recognition_ai/constants.py"

FRIGATE_PASSWORD = os.getenv("FRIGATE_PASSWORD", "example_password")  # noqa
# face training
FACE_TRAINING_CONTAINER_NAME = os.getenv(  # noqa
    "FACE_TRAINING_CONTAINER_NAME", "face_training"
)

# Vehicle ai
VEHICLE_CONFIG_NAME = os.getenv("VEHICLE_CONFIG_NAME", "number_plate_detection")  # noqa

VEHICLE_CONFIG_PATH = f"{FOLDER_ROOT_PATH}/pilot_vehicle_ai/constants.py"

# Loitering ai
LOITERING_CONFIG_NAME = os.getenv(  # noqa
    "LOITERING_CONFIG_NAME", "loiter_detection"
)

# Webdriver
WEBDRIVER_LOCATION_SERVICE = os.getenv(  # noqa
    "WEBDRIVER_LOCATION_SERVICE", "/usr/bin/chromedriver"
)  # noqa

# Home Assistant
HASS_URL = os.getenv("HASS_URL", "http://localhost:8123")  # noqa
HASS_USERNAME = os.getenv("HASS_USERNAME", "root")  # noqa
HASS_PASSWORD = os.getenv(  # noqa
    "HASS_PASSWORD",
    "873df0ed8280d2670b7df93954125dbed1d882d72586a42d205e1de440ae1b99b1dd6f02",
)

WIFI_CREDENTIALS_PATH = f"{FOLDER_ROOT_PATH}/credentials/hub_credentials.json"

CLOUDFLARED_HOST_PATH = f"{FOLDER_ROOT_PATH}/cloudflared_domain.txt"

PERSON_DATA_PATH = f"{FOLDER_ROOT_PATH}/pilot_face_train_ai/person_data.json"

# Hub Operations
JUPYTER_HOST = os.getenv("JUPYTER_HOST", "https://api.hub.jupyter.com.au")  # noqa
HUB_DELETE_URL = os.getenv("HUB_DELETE_URL", "/hub/removed")  # noqa

TRAINING_RESULT_FILE = f"{FOLDER_ROOT_PATH}/pilot_face_train_ai/result.txt"
TRAINING_NAME_FILE = (
        Path(os.getcwd()).parent / "pilot_face_train_ai/person_name.txt"  # noqa
)  # noqa
TRAINING_FOLDER_PATH = f"{FOLDER_ROOT_PATH}/pilot_face_train_ai/media"
RECOGNIZE_FOLDER_PATH = f"{FOLDER_ROOT_PATH}/pilot_face_recognition_ai"
ENV_FILE = "/root/jupyter-hub-controller/.env"

# Alarm Settings

# Base URL for media files served from HA's /config/www/ directory.
# This MUST be reachable from the Halo over LAN (not localhost).
# Set via env var per hub — the Halo fetches this URL directly over HTTP.
# Example: http://192.168.1.225:8123 (Vancouver), http://192.168.1.26:8123 (Melbourne)
HASS_MEDIA_BASE_URL = os.getenv(  # noqa
    "HASS_MEDIA_BASE_URL",
    os.getenv("HASS_URL", "http://localhost:8123"),
)

# Sound files served locally from HA /config/www/ via plain HTTP.
# NO TLS overhead — prevents ESP32 OOM crash (40-60KB heap saved).
# Files: MP3 mono 44.1kHz 128kbps for ESPHome announcement_pipeline compatibility.
ALARM_SOUND_URL = os.getenv(  # noqa
    "ALARM_SOUND_URL",
    f"{HASS_MEDIA_BASE_URL}/local/alarm.mp3",
)

BARKING_DOGS_SOUND_URL = os.getenv(  # noqa
    "BARKING_DOG_SOUND_URL",
    f"{HASS_MEDIA_BASE_URL}/local/dog_mono.mp3",
)
PEOPLE_HOME_SOUND_URL = os.getenv(  # noqa
    "PARTY_SOUND_URL",
    f"{HASS_MEDIA_BASE_URL}/local/people_mono.mp3",
)

RUNNING_APPLIANCES_SOUND_URL = os.getenv(  # noqa
    "RUNNING_APPLIANCES_SOUND_URL",
    f"{HASS_MEDIA_BASE_URL}/local/vacuum_mono.mp3",
)

HASS_WEBSOCKET_URL = os.getenv(  # noqa
    "HASS_WEBSOCKET_URL", "ws://localhost:8123/api/websocket"
)
MEROSS_CLOUD_PER = os.getenv("MEROSS_CLOUD_PER", 1)  # noqa

BASE_DIR_VIDEO = str(Path(os.getcwd()).parent / "upload")  # noqa

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # noqa
STATIC_URL = "/static/"
BASE_DIR_FILE = f"{FOLDER_ROOT_PATH}/upload"
STATIC_ROOT = os.path.join(BASE_DIR, "staticfiles")  # noqa
STATICFILES_DIRS = [os.path.join(BASE_DIR, "static"), BASE_DIR_FILE]  # noqa
STATICFILES_FINDERS = (
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
    "django.contrib.staticfiles.finders.FileSystemFinder",
)

MEDIA_URL = "/media/"

MEDIA_ROOT = f"{FOLDER_ROOT_PATH}/frigate/storage"

HASS_MQTT_TOPIC_PUBLISH_ALARM = os.getenv(  # noqa
    "HASS_MQTT_TOPIC_PUBLISH_ALARM", "/live_activity_prompt"
)
HASS_MQTT_TOPIC_LISTEN_EVENT_AUTOMATION = os.getenv(  # noqa
    "HASS_MQTT_TOPIC_LISTEN_EVENT_AUTOMATION", "/events"
)

HASS_MQTT_TOPIC_CONTROL_MANUAL_ALARM_DEVICE = os.getenv(  # noqa
    "HASS_MQTT_TOPIC_CONTROL_MANUAL_ALARM_DEVICE", "/control_manual_alarm"
)
HASS_MQTT_TOPIC_PUBLISH_CARD_GARAGE = os.getenv(  # noqa
    "HASS_MQTT_TOPIC_PUBLISH_CARD_GARAGE", "garage_card"
)

HASS_MQTT_TOPIC_CONTROL_GARAGE_DEVICE = os.getenv(  # noqa
    "HASS_MQTT_TOPIC_CONTROL_GARAGE_DEVICE", "control_garage"
)

HASS_MQTT_TOPIC_LISTEN_UNSUAL_SOUND_AUTOMATION = os.getenv(  # noqa
    "HASS_MQTT_TOPIC_LISTEN_UNUSUAL_SOUND", "/events_unusual_sound"
)

HASS_MQTT_TOPIC_LISTEN_SPEAK_VOICE_AI = os.getenv(  # noqa
    "HASS_MQTT_TOPIC_LISTEN_SPEAK_VOICE_AI", "/events_alarm"
)

HASS_MQTT_TOPIC_LISTEN_VEHICLE_ALARM = os.getenv(  # noqa
    "HASS_MQTT_TOPIC_LISTEN_VEHICLE_ALARM", "/events_vehicle_alarm"
)

HASS_MQTT_TOPIC_LISTEN_PARCEL_THEFT_AUTOMATION = os.getenv(  # noqa
    "HASS_MQTT_TOPIC_LISTEN_PARCEL_THEFT", "/events_parcel_theft"
)

HASS_MQTT_TOPIC_LISTEN_LOITERING_AUTOMATION = os.getenv(  # noqa
    "HASS_MQTT_TOPIC_LISTEN_LOITERING", "/events_loitering"
)

HASS_MQTT_TOPIC_LISTEN_EVENT_TURN_OFF_AUTOMATION = os.getenv(  # noqa
    "HASS_MQTT_TOPIC_LISTEN_TURN_OFF", "/events_turn_off"
)

HASS_MQTT_TOPIC_NOTIFICATION_START_ALARM = os.getenv(  # noqa
    "HASS_MQTT_TOPIC_NOTIFICATION_START_ALARM", "/start_alarm"
)

HASS_MQTT_TOPIC_NOTIFICATION_STOP_ALARM = os.getenv(  # noqa
    "HASS_MQTT_TOPIC_NOTIFICATION_STOP_ALARM", "/stop_alarm"
)

HASS_MQTT_TOPIC_NOTIFICATION_COUNTDOWN_ALARM = os.getenv(  # noqa
    "HASS_MQTT_TOPIC_NOTIFICATION_COUNTDOWN_ALARM", "/countdown"
)

HASS_MQTT_TOPIC_EVENT_ALARM_ACTIVE = os.getenv(  # noqa
    "HASS_MQTT_TOPIC_EVENT_ALARM_ACTIVE", "/event_alarm_active"
)

MEDIAMTX_CONTAINER_NAME = "mediamtx"
MEDIAMTX_CONFIG_PATH = "/root/mediamtx/mediamtx.yml"

RING_STREAM_CONTAINER = os.getenv("RING_STREAM_CONTAINER", "ring_mqtt")  # noqa

REMOTE_HOST = (
    f"{os.getenv('DEVICE_NAME', 'hub').strip()}."  # noqa
    f"{os.getenv('FRPS_URL', 'hub.dev.jupyter.com.au').strip()}"  # noqa
)

ICE_SERVER_URL = "/hub/ice-server"
RING_STREAM_CONFIG_PATH = f"{FOLDER_ROOT_PATH}/ring-mqtt-data/config.json"
RING_STREAM_CONFIG_STATE_PATH = f"{FOLDER_ROOT_PATH}/ring-mqtt-data/ring-state.json"

MQTT_RING_CAMERA_PASSWORD = os.getenv(  # noqa
    "MQTT_RING_CAMERA_PASSWORD", "ring_camera_password"
)

DEVICE_NAME = os.getenv("DEVICE_NAME", "hub")  # noqa
DEVICE_SECRET = os.getenv("DEVICE_SECRET", "secrecy")  # noqa
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": CELERY_BROKER_URL,
    }
}
API_ALARM_MODE_KEY = os.getenv(  # noqa
    "API_ALARM_MODE_KEY", "dsahjkxfgbcdwkjsfndslkxcfjmds"
)

WAKE_WORK_CONTAINER = os.getenv("WAKE_WORK_CONTAINER", "jupyter_voice_ai")  # noqa
SOUND_DETECTION_CONTAINER = os.getenv(  # noqa
    "SOUND_DETECTION_CONTAINER", "sound_detection"
)
SOUND_DETECTION_PATH = (
    f"{FOLDER_ROOT_PATH}/pilot_unusual_sounds_ai/constants.py"  # noqa
)
FRV_API_KEY = "frv_2026_9cA7F2kM8QeLwVYxR4dB3HnJpS6ZtU"
