"""
Local settings
"""

from .common import *  # noqa

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/3.2/howto/deployment/checklist/

DEBUG = True

SECRET_KEY = "django-insecure-*$0b8ibx7uzk45cm+fxw7*jj(yzi2ye!l4+!dnyxa-u-nbuz=q"

ALLOWED_HOSTS = ["*"]

HOST = "http://localhost:8000/"

# Database
# https://docs.djangoproject.com/en/3.2/ref/settings/#databases

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql_psycopg2",
        "NAME": "hub_controller",
        "USER": "postgres",
        "PASSWORD": "3acc5c52d8b7931042d464e1118057744f1af274cb6278254134333198788e23dac5b418",
        "HOST": "localhost",
        "PORT": "5433",
    }
}

# CORS config
CORS_ALLOWED_ORIGINS = ["http://localhost:8000"]

# Logging
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {filename}:{lineno} >>> {message}",
            "style": "{",
        },
        "simple": {
            "format": "{levelname} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "django.db": {
            # django also has database level logging
            "level": "INFO"
        },
    },
}

# Frigate
FRIGATE_CONFIG_PATH = "/root/jupyter-container/frigate/config/config.yaml"
RTSP_PASSWORD = "example_password"
RING_CONVERTER_CONFIG_PATH = "../jupyter-ring-camera-converter/cameras.json"
FRIGATE_CONTAINER_NAME = "frigate"

# Celery
CELERY_BROKER_URL = os.getenv(  # noqa
    "BROKER_URL", "redis://default:example@localhost:6379/0"
)
CELERY_RESULT_BACKEND = os.getenv(  # noqa
    "BROKER_URL", "redis://default:example@localhost:6379/0"
)

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
MQTT_HOST = "localhost"
MQTT_PORT = 1883
MQTT_USERNAME = "controller"
MQTT_PASSWORD = os.getenv("MQTT_CONTROLLER_PASSWORD", "example_password")  # noqa

MQTT_FRIGATE_USERNAME = "frigate"  # noqa
MQTT_FRIGATE_PASSWORD = os.getenv("MQTT_FRIGATE_PASSWORD", "example_password")  # noqa
FRIGATE_MQTT_TOPIC = os.getenv("FRIGATE_MQTT_TOPICE", "frigate/events")  # noqa
FRIGATE_PASSWORD = os.getenv("FRIGATE_PASSWORD", "example_password")  # noqa
# SCRYPTED
SCRYPTED_HOST = "localhost:10443"
SCRYPTED_PASSWORD = "example_password"
SCRYPTED_USERNAME = "hub"

# Parcel detect ai

PARCEL_CONFIG_PATH = "/root/jupyter-container/pilot_parcel_theft_AI/constants.py"
PARCEL_BOX_CONFIG_PATH = os.getenv(  # noqa
    "PARCEL_BOX_CONFIG_PATH",
    Path(os.getcwd()).parent  # noqa
    / "pilot_parcel_theft_AI/bounding_box.json",  # noqa
)
PARCEL_CONTAINER_NAME = os.getenv("PARCEL_CONTAINER_NAME", "parcel_detection")  # noqa

# facial ai
FACIAL_CONTAINER_NAME = os.getenv("FACIAL_CONTAINER_NAME", "face_recognition")  # noqa

FACIAL_CONFIG_PATH = "jupyter-container/pilot_face_recognition_ai/constants.py"

# face training
FACE_TRAINING_CONTAINER_NAME = os.getenv(  # noqa
    "FACE_TRAINING_CONTAINER_NAME", "face_training"
)

# Vehicle ai
VEHICLE_CONFIG_NAME = os.getenv("VEHICLE_CONFIG_NAME", "number_plate_detection")  # noqa

VEHICLE_CONFIG_PATH = "/root/jupyter-container/pilot_vehicle_ai/constants.py"

# Loitering ai
LOITERING_CONFIG_NAME = os.getenv(  # noqa
    "LOITERING_CONFIG_NAME", "loitery_classification_service"
)
LOITERING_CONFIG_PATH = os.getenv(  # noqa
    "LOITERING_CONFIG_PATH",
    Path(os.getcwd()).parent / "secureprotect_loiteringai_mvp/constants.py",  # noqa
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
    "5c04d7cf37a70506741d48fc92a72a75508b8d66d15bbf2a231b5d2cfc9ecc2af5d041ce",
)

# Wi-Fi credentials
WIFI_CREDENTIALS_PATH = "/root/jupyter-container/credentials/hub_credentials.json"

CLOUDFLARED_HOST_PATH = "/root/jupyter-container/cloudflared_domain.txt"

PERSON_DATA_PATH = "/root/jupyter-container/pilot_face_train_ai/person_data.json"

# Hub Operations
JUPYTER_HOST = os.getenv("JUPYTER_HOST", "https://api.hub.jupyter.com.au")  # noqa
HUB_DELETE_URL = "/hub/removed"  # noqa

TRAINING_RESULT_FILE = "/root/jupyter-container/pilot_face_train_ai/result.txt"
TRAINING_NAME_FILE = (
    Path(os.getcwd()).parent / "face_trainingi/person_name.txt"  # noqa
)  # noqa
TRAINING_FOLDER_PATH = "/root/jupyter-container/pilot_face_train_ai/media"

RECOGNIZE_FOLDER_PATH = "/root/jupyter-container/pilot_face_recognition_ai"

ENV_FILE = "/root/jupyter-hub-controller/.env"

TRAINING_FOLDER_PATH = Path(os.getcwd()).parent / "face_training/media"  # noqa  # noqa

ENV_FILE = Path(os.getcwd()).parent / ".env"  # noqa
# Alarm Settings

ALARM_SOUND_URL = os.getenv(  # noqa
    "ALARM_SOUND_URL",
    "https://jupyter-dev-g698wtqso2dmv0px-images-bucket.s3.ap-southeast-2.amazonaws.com/alarm.mp3",
)

BARKING_DOGS_SOUND_URL = os.getenv(  # noqa
    "BARKING_DOG_SOUND_URL",
    "https://jupyter-dev-g698wtqso2dmv0px-images-bucket.s3.ap-southeast-2.amazonaws.com/barking_dogs.mp3",
)
PEOPLE_HOME_SOUND_URL = os.getenv(  # noqa
    "PARTY_SOUND_URL",
    "https://jupyter-dev-g698wtqso2dmv0px-images-bucket.s3.ap-southeast-2.amazonaws.com/people_home.mp3",
)

RUNNING_APPLIANCES_SOUND_URL = os.getenv(  # noqa
    "MRUNNING_APPLIANCES_SOUND_URL",
    "https://jupyter-dev-g698wtqso2dmv0px-images-bucket.s3.ap-southeast-2.amazonaws.com/running_appliances.mp3",
)


HASS_WEBSOCKET_URL = os.getenv(  # noqa
    "HASS_WEBSOCKET_URL", "ws://localhost:8123/api/websocket"
)
MEROSS_CLOUD_PER = os.getenv("MEROSS_CLOUD_PER", 1)  # noqa

BASE_DIR_VIDEO = str(Path(os.getcwd()).parent / "upload")  # noqa
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # noqa
STATIC_URL = "/static/"

MEDIA_URL = "/media/"
MEDIA_ROOT = "/root/jupyter-container/frigate/storage"
STATIC_ROOT = os.path.join(BASE_DIR, "staticfiles")  # noqa
BASE_DIR_FILE = "/root/jupyter-container/upload"
STATICFILES_DIRS = [
    os.path.join(BASE_DIR, "static"),  # noqa
    BASE_DIR_FILE,
    BASE_DIR_VIDEO,
]
STATICFILES_FINDERS = (
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
    "django.contrib.staticfiles.finders.FileSystemFinder",
)

HASS_MQTT_TOPIC_PUBLISH_ALARM = os.getenv(  # noqa
    "HASS_MQTT_TOPIC_PUBLISH_ALARM", "/live_activity_prompt"
)
HASS_MQTT_TOPIC_LISTEN_EVENT_AUTOMATION = os.getenv(  # noqa
    "HASS_MQTT_TOPIC_LISTEN_EVENT_AUTOMATION", "/events"
)
HASS_MQTT_TOPIC_LISTEN_EVENT_TURN_OFF_AUTOMATION = os.getenv(  # noqa
    "HASS_MQTT_TOPIC_LISTEN_EVENT_AUTOMATION", "/events_turn_off"
)

HASS_MQTT_TOPIC_CONTROL_MANUAL_ALARM_DEVICE = os.getenv(  # noqa
    "HASS_MQTT_TOPIC_CONTROL_MANUAL_ALARM_DEVICE ", "/control_manual_alarm"
)

HASS_MQTT_TOPIC_PUBLISH_CARD_GARAGE = os.getenv(  # noqa
    "HASS_MQTT_TOPIC_PUBLISH_CARD_GARAGE", "garage_card"
)

HASS_MQTT_TOPIC_CONTROL_GARAGE_DEVICE = os.getenv(  # noqa
    "HASS_MQTT_TOPIC_CONTROL_GARAGE_DEVICE", "control_garage"
)

HASS_MQTT_TOPIC_LISTEN_UNSUAL_SOUND_AUTOMATION = os.getenv(  # noqa
    "HASS_MQTT_TOPIC_LISTEN_EVENT_AUTOMATION", "/events_unusual_sound"
)

HASS_MQTT_TOPIC_LISTEN_PARCEL_THEFT_AUTOMATION = os.getenv(  # noqa
    "HASS_MQTT_TOPIC_LISTEN_EVENT_AUTOMATION", "/events_parcel_theft"
)

HASS_MQTT_TOPIC_LISTEN_VEHICEL_ALARM = os.getenv(  # noqa
    "HASS_MQTT_TOPIC_LISTEN_VEHICEL_ALARM", "/events_vehicel_alarm"
)

HASS_MQTT_TOPIC_LISTEN_SPEAK_VOICE_AI = os.getenv(  # noqa
    "HASS_MQTT_TOPIC_LISTEN_SPEAK_VOICE_AI", "/events_alarm"
)

HASS_MQTT_TOPIC_LISTEN_LOITERING_AUTOMATION = os.getenv(  # noqa
    "HASS_MQTT_TOPIC_LISTEN_EVENT_AUTOMATION", "/events_loitering"
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

REMOTE_HOST = (
    f"{os.getenv('DEVICE_NAME', 'hub').strip()}."  # noqa
    f"{os.getenv('FRPS_URL', 'hub.dev.jupyter.com.au').strip()}"  # noqa
)

ICE_SERVER_URL = "/hub/ice-server"
RING_STREAM_CONFIG_PATH = "/root/jupyter-container/ring_mqtt_data/config.json"
RING_STREAM_CONFIG_STATE_PATH = "/root/jupyter-container/ring_mqtt_data/ring-state.json"

MQTT_RING_CAMERA_PASSWORD = os.getenv(  # noqa
    "MQTT_RING_CAMERA_PASSWORD", "ring_camera_password"
)

DEVICE_NAME = os.getenv("DEVICE_NAME", "hub")  # noqa
DEVICE_SECRET = os.getenv("DEVICE_SECRET", "secrecy")  # noqa
RING_STREAM_CONTAINER = os.getenv("RING_STREAM_CONTAINER", "ring_mqtt")  # noqa

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": CELERY_BROKER_URL,
    }
}

API_ALARM_MODE_KEY = os.getenv(  # noqa
    "API_ALARM_MODE_KEY", "dsahjkxfgbcdwkjsfndslkxcfjmds"
)

WAKE_WORK_CONTAINER = os.getenv("WAKE_WORK_CONTAINER", "wake_word")  # noqa
SOUND_DETECTION_CONTAINER = os.getenv(  # noqa
    "SOUND_DETECTION_CONTAINER", "sound_detection"
)
SOUND_DETECTION_PATH = (
    "/root/jupyter-container/pilot_unusual_sounds_ai/constants.py"  # noqa
)
FRV_API_KEY = "frv_2026_9cA7F2kM8QeLwVYxR4dB3HnJpS6ZtU"