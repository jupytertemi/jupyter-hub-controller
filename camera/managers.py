import logging
import os
import re
import socket
import subprocess
import uuid

from django.apps import apps
from django.conf import settings
from django.db import models
from django.utils.text import slugify
from django_celery_beat.models import IntervalSchedule, PeriodicTask
from onvif import ONVIFCamera, ONVIFError
from portscan import PortScan
from rest_framework.exceptions import ValidationError

from camera.enums import CameraType
from camera.tasks import camera_setting_config, update_camera_config
from utils.restarting_service import restart_service


class RTSPCameraManager(models.Manager):
    checking_retries = 3
    checking_timeout = 10

    def get_mac_address(self, ip_address):
        pid = os.popen(f"arp -n {ip_address}")
        s = pid.read()
        pid.close()
        match = re.search(r"(([a-f\d]{1,2}:){5}[a-f\d]{1,2})", s, re.I)
        if match:
            return match.group(0)
        else:
            return None

    def get_camera_organization(self, mac_address_prefixes):
        CameraOrganization = apps.get_model(
            "camera.cameraorganization",
        )

        return CameraOrganization.objects.filter(
            mac_address_prefix__in=mac_address_prefixes
        ).values_list("mac_address_prefix", "organization_name")

    ONVIF_PORTS = [80, 2020, 8080, 8899]

    def get_onvif_name(self, ip):
        for port in self.ONVIF_PORTS:
            try:
                camera = ONVIFCamera(ip, port, "", "")
                device_management = camera.create_devicemgmt_service()
                device_info = device_management.GetDeviceInformation()
                return f"{device_info.Manufacturer} {device_info.Model}"
            except Exception:
                continue
        return None

    def get_onvif_device_info(self, ip, username="", password=""):
        """Return {'manufacturer': ..., 'model': ...} or None from ONVIF.

        Retries up to 3 times with 2s delay -- camera ONVIF services often
        boot slower than RTSP, causing intermittent failures on first attempt.
        """
        import time as _time
        for port in self.ONVIF_PORTS:
            for attempt in range(2):
                try:
                    camera = ONVIFCamera(ip, port, username, password)
                    device_management = camera.create_devicemgmt_service()
                    device_info = device_management.GetDeviceInformation()
                    return {
                        "manufacturer": (device_info.Manufacturer or "").strip(),
                        "model": (device_info.Model or "").strip(),
                    }
                except Exception as err:
                    logging.info(f"get_onvif_device_info port {port} attempt {attempt+1}/2 failed for {ip}: {err}")
                    if attempt < 1:
                        _time.sleep(2)
        return None

    def get_discover_name(self, items):
        list_mac_address = [
            {item["ip"]: self.get_mac_address(item["ip"])[:8].replace(":", "-").upper()}
            for item in items
            if self.get_mac_address(item["ip"])
        ]
        list_camera_organization = dict(
            self.get_camera_organization(
                [list(item.values())[0] for item in list_mac_address]
            )
        )

        for item in items:
            onvif_name = self.get_onvif_name(item["ip"])
            if onvif_name:
                item["name"] = onvif_name
            else:
                value = next(
                    (
                        v
                        for d in list_mac_address
                        if item["ip"] in d
                        for v in d.values()
                    ),
                    None,
                )
                if list_camera_organization.get(value, None):
                    item["name"] = list_camera_organization.get(value)
        return items

    def get_queryset(self, *args, **kwargs):
        queryset = super().get_queryset(*args, **kwargs)
        queryset = queryset.filter(type=CameraType.RTSP)
        return queryset

    def discover(
        self,
    ):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        subnet = ".".join(ip.split(".")[:3])
        print(f"Discovering {subnet}...")

        portscan = PortScan(subnet + ".0/24", "554")
        results = portscan.run()
        sock.close()
        items = [{"ip": item[0], "name": None} for item in results if item]

        data = self.get_discover_name(items)
        return data

    def generate_slug_name(self, name):
        my_uuid = uuid.uuid4()
        unique_string = f"{slugify(name)}-{str(my_uuid)[0:6]}"
        return unique_string.lower()

    def _capture_thumbnail(self, slug_name, rtsp_url):
        """Grab one RTSP frame via ffmpeg and save as zone-drawing thumbnail."""
        thumbnails_dir = "/root/jupyter-hub-controller/media/thumbnails"
        os.makedirs(thumbnails_dir, exist_ok=True)
        dst = os.path.join(thumbnails_dir, f"{slug_name}.jpg")
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-rtsp_transport", "tcp", "-i", rtsp_url,
                 "-frames:v", "1", "-q:v", "2", dst],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=10, check=False,
            )
        except subprocess.TimeoutExpired:
            pass

    def create(self, **kwargs):
        slug_name = self.generate_slug_name(kwargs.get("name"))
        kwargs["slug_name"] = slug_name

        # Capture MAC address before saving
        ip = kwargs.get("ip")
        if ip and not kwargs.get("mac_address"):
            from alarm.network import get_mac_address
            mac = get_mac_address(ip)
            if mac:
                kwargs["mac_address"] = mac

        # Auto-derive sub-stream URL if not already set by get_rtsp_url()
        rtsp_url = kwargs.get("rtsp_url")
        if rtsp_url and not kwargs.get("sub_rtsp_url"):
            kwargs["sub_rtsp_url"] = self._derive_sub_stream_url(rtsp_url)

        # Save snapshot for zone drawing before Frigate restarts
        if rtsp_url:
            self._capture_thumbnail(slug_name, rtsp_url)

        # Pop fields not in the Camera model before creating
        sub_rtsp_url = kwargs.pop("sub_rtsp_url", None)
        kwargs.pop("mac_address", None)

        camera = super().create(**kwargs)
        update_camera_config.delay()
        self.create_ip_monitor_task()
        return camera

    def create_ip_monitor_task(self):
        """Register Celery Beat task to monitor RTSP camera IPs every 5 minutes."""
        schedule, _ = IntervalSchedule.objects.get_or_create(
            every=5, period=IntervalSchedule.MINUTES
        )
        PeriodicTask.objects.get_or_create(
            name="monitor_camera_ips",
            defaults={
                "task": "camera.tasks.monitor_camera_ips",
                "interval": schedule,
                "queue": "automation_queue",
                "enabled": True,
            },
        )

    def delete_ip_monitor_task(self):
        """Remove the periodic task when no RTSP cameras remain."""
        PeriodicTask.objects.filter(name="monitor_camera_ips").delete()

    def _derive_sub_stream_url(self, main_url):
        """Derive sub-stream URL from main stream URL using brand-specific patterns.

        Tier 2 fallback when ONVIF only returns one profile.
        Returns None if no pattern matches (Tier 3: main stream used for both).
        """
        if not main_url:
            return None

        # TP-Link ViGi / Uniview: /stream1 → /stream2
        if '/stream1' in main_url:
            return main_url.replace('/stream1', '/stream2')

        # Hikvision / HiLook / EZVIZ: /Streaming/Channels/X01 → X02
        match = re.search(r'(Streaming/Channels/\d)01', main_url)
        if match:
            return main_url.replace(match.group(0), match.group(1) + '02')

        # Dahua / Amcrest: subtype=0 → subtype=1
        if 'subtype=0' in main_url:
            return main_url.replace('subtype=0', 'subtype=1')

        # Reolink: _main → _sub
        if '_main' in main_url:
            return main_url.replace('_main', '_sub')

        # UniFi / Ubiquiti: /s0 → /s1
        if main_url.endswith('/s0') or '/s0?' in main_url:
            return main_url.replace('/s0', '/s1', 1)

        # Foscam: /videoMain → /videoSub
        if '/videoMain' in main_url:
            return main_url.replace('/videoMain', '/videoSub')

        # D-Link: /live1.sdp → /live2.sdp
        if '/live1.sdp' in main_url:
            return main_url.replace('/live1.sdp', '/live2.sdp')

        # Sony / Vivotek: /video1 → /video2  or /live.sdp → /live2.sdp
        if '/video1' in main_url:
            return main_url.replace('/video1', '/video2')
        if '/live.sdp' in main_url:
            return main_url.replace('/live.sdp', '/live2.sdp')

        # Samsung / Hanwha: /profile2/media.smp → /profile3/media.smp
        if '/profile2/media.smp' in main_url:
            return main_url.replace('/profile2/media.smp', '/profile3/media.smp')

        # Panasonic: /MediaInput/h264 → /MediaInput/h264/stream_2
        if '/MediaInput/h264' in main_url and 'stream_2' not in main_url:
            return main_url.replace('/MediaInput/h264', '/MediaInput/h264/stream_2')

        # No pattern matched — Tier 3: return None, template falls back to main
        return None

    def get_rtsp_url(self, kwargs):
        try:

            ip = kwargs.get("ip")
            username = kwargs.get("username")
            password = kwargs.get("password")

            # Verify username, passwork, ip of camera rtsp
            self.rtsp_validate(ip, username, password)
            # Create the camera object
            camera_fc = None
            for _port in self.ONVIF_PORTS:
                try:
                    camera_fc = ONVIFCamera(ip, _port, username, password)
                    camera_fc.create_devicemgmt_service()
                    break
                except Exception:
                    camera_fc = None
            if camera_fc is None:
                raise ONVIFError("Could not connect to ONVIF on any port")
            # Create the media service
            media_service = camera_fc.create_media_service()
            # Get the profiles
            profiles = camera_fc.media.GetProfiles()
            # Use the first profile
            profile = profiles[0]
            # Get the stream URI
            stream_uri = media_service.GetStreamUri(
                {
                    "StreamSetup": {"Stream": "RTP-Unicast", "Transport": "RTSP"},
                    "ProfileToken": profile.token,
                }
            )
            url = stream_uri.Uri.replace("rtsp://", f"rtsp://{username}:{password}@")
            url = url.split("?", 1)[0]

            # Tier 1: Discover sub-stream from ONVIF profiles[1]
            sub_url = None
            if len(profiles) > 1:
                try:
                    sub_stream_uri = media_service.GetStreamUri(
                        {
                            "StreamSetup": {"Stream": "RTP-Unicast", "Transport": "RTSP"},
                            "ProfileToken": profiles[1].token,
                        }
                    )
                    sub_url = sub_stream_uri.Uri.replace("rtsp://", f"rtsp://{username}:{password}@")
                    sub_url = sub_url.split("?", 1)[0]
                    # Verify it's actually different from main stream
                    if sub_url == url:
                        sub_url = None
                except Exception:
                    pass

            # Tier 2: Derive sub-stream from main URL using brand patterns
            if not sub_url:
                sub_url = self._derive_sub_stream_url(url)

            # Tier 3: sub_url stays None — template falls back to main stream
            # Store in kwargs (passed by reference) so create() receives it
            kwargs['sub_rtsp_url'] = sub_url

            # Gather ONVIF device info (manufacturer/model) for auto-populate
            onvif_manufacturer = ''
            onvif_model = ''
            try:
                device_service = camera_fc.create_devicemgmt_service()
                device_info = device_service.GetDeviceInformation()
                onvif_manufacturer = getattr(device_info, 'Manufacturer', '') or ''
                onvif_model = getattr(device_info, 'Model', '') or ''
            except Exception as e:
                logging.warning(f"ONVIF GetDeviceInformation failed for {ip}: {e}")

            return {
                'rtsp_url': url,
                'sub_rtsp_url': sub_url,
                'onvif_manufacturer': onvif_manufacturer,
                'onvif_model': onvif_model,
                'message': 'Get camera rtsp url successfully.',
            }
        except ONVIFError as err:
            logging.error(f"error:{err}")
            return None

    def rtsp_validate(self, ip, username, password):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(5)
                if sock.connect_ex((ip, 554)) != 0:
                    raise ValidationError({"detail": f"Camera IP {ip} is incorrect"})
        except ValidationError:
            raise
        except Exception:
            logging.error(f"Camera IP {ip} is unreachable on port 554")
            raise ValidationError({"detail": f"Camera IP {ip} is incorrect"})

class RingCameraManager(models.Manager):
    def get_queryset(self, *args, **kwargs):
        queryset = super().get_queryset(*args, **kwargs)
        queryset = queryset.filter(type=CameraType.RING)
        return queryset

    def generate_slug_name(self, name):
        my_uuid = uuid.uuid4()
        unique_string = f"{slugify(name)}-{str(my_uuid)[0:6]}"
        return unique_string.lower()

    def create(self, **kwargs):
        slug_name = self.generate_slug_name(kwargs.get("name"))
        ring_device_id = kwargs.get("ring_device_id")
        kwargs["slug_name"] = slug_name
        kwargs["rtsp_url"] = f"rtsp://ring-mqtt:8554/{ring_device_id}_live"
        # Pop fields not in the Camera model before creating
        sub_rtsp_url = kwargs.pop("sub_rtsp_url", None)
        kwargs.pop("mac_address", None)

        camera = super().create(**kwargs)
        restart_service(settings.RING_STREAM_CONTAINER)
        update_camera_config.delay()
        self.create_restart_ring_task()
        return camera

    def create_restart_ring_task(self):
        """
        Create a single Beat task for restarting Ring MQTT every 10 minutes.
        If task already exists, do nothing.
        """
        schedule, _ = IntervalSchedule.objects.get_or_create(
            every=20, period=IntervalSchedule.MINUTES
        )

        task, created = PeriodicTask.objects.get_or_create(
            name="restart_ring_task",
            defaults={
                "task": "camera.tasks.restart_ring_safe",
                "interval": schedule,
                "enabled": True,
            },
        )
        return task, created

    def delete_restart_ring_task(self):
        """
        Delete the Beat task safely (e.g., when all cameras are removed)
        """
        PeriodicTask.objects.filter(name="restart_ring_task").delete()


class CameraSettingManager(models.Manager):

    def update(self, instance, validated_data):
        updated_fields = []
        loitering_cameras_data = validated_data.pop("loitering_cameras", None)

        for field, value in validated_data.items():
            if hasattr(instance, field):
                current_value = getattr(instance, field)
                if current_value != value:
                    setattr(instance, field, value)
                    updated_fields.append(field)

        if loitering_cameras_data is not None:
            instance.save()
            instance.loitering_cameras.set(loitering_cameras_data)
            updated_fields.append("loitering_cameras")

        if (
            "enable_parcel_detect" in updated_fields
            or "parcel_detect_camera" in updated_fields
        ):
            camera_name = None
            if instance.enable_parcel_detect:
                camera_name = instance.parcel_detect_camera.slug_name

            self.handle_parcel_detect(instance.enable_parcel_detect, camera_name)

        if "enable_face_recognition" in updated_fields:
            self.handle_face_recognition(instance.enable_face_recognition)

        if "license_vehicle_recognition" in updated_fields:
            camera_name = None
            if instance.license_vehicle_recognition:
                camera_name = instance.vehicle_recognition_camera.slug_name
            self.handle_license_vehicle_recognition(
                instance.license_vehicle_recognition, camera_name
            )
        if (
            "loitering_recognition" in updated_fields
            or "loitering_camera" in updated_fields
            or "loitering_cameras" in updated_fields
        ):
            camera_names = self._get_loiter_camera_names(instance)
            self.handle_loitering_recognition(
                instance.loitering_recognition, camera_names
            )
        if "activate_sounds_detection" in updated_fields:
            self.handle_sounds_detection(instance.activate_sounds_detection)

        if "footage_retention_period" in updated_fields:
            self.handle_retention_period()
        instance.save()

        return instance

    def _get_loiter_camera_names(self, instance):
        camera_names = None
        if instance.loitering_recognition:
            m2m_cameras = list(instance.loitering_cameras.all())
            if m2m_cameras:
                camera_names = ",".join(c.slug_name for c in m2m_cameras)
            elif instance.loitering_camera:
                camera_names = instance.loitering_camera.slug_name
        return camera_names

    def handle_parcel_detect(self, is_enabnled: bool, camera_name=None):
        container_name = settings.PARCEL_CONTAINER_NAME
        servicer_path = settings.PARCEL_CONFIG_PATH
        camera_setting_config.apply_async(
            args=(is_enabnled, container_name, str(servicer_path), camera_name),
            queue="camera_queue",
        )

    def handle_face_recognition(self, is_enabnled: bool):
        container_name = settings.FACIAL_CONTAINER_NAME
        servicer_path = settings.FACIAL_CONFIG_PATH
        camera_setting_config.apply_async(
            args=(is_enabnled, container_name, str(servicer_path)), queue="camera_queue"
        )

    def handle_license_vehicle_recognition(self, is_enabnled: bool, camera_name=None):
        container_name = settings.VEHICLE_CONFIG_NAME
        servicer_path = settings.VEHICLE_CONFIG_PATH
        camera_setting_config.apply_async(
            args=(is_enabnled, container_name, str(servicer_path), camera_name),
            queue="camera_queue",
        )

    def handle_loitering_recognition(self, is_enabnled: bool, camera_name=None):
        container_name = settings.LOITERING_CONFIG_NAME
        servicer_path = settings.LOITERING_CONFIG_PATH
        camera_setting_config.apply_async(
            args=(is_enabnled, container_name, str(servicer_path), camera_name),
            queue="camera_queue",
        )

    def handle_sounds_detection(self, is_enabnled: bool):
        container_name = settings.SOUND_DETECTION_CONTAINER
        servicer_path = settings.SOUND_DETECTION_PATH
        camera_setting_config.apply_async(
            args=(is_enabnled, container_name, str(servicer_path)),
            queue="camera_queue",
        )

    def handle_retention_period(self):
        # Custom logic for footage retention period
        logging.info("Footage retention period updated!")
