import json
import logging
import os
import re
import socket
import struct
import subprocess
import uuid


def read_jpeg_dimensions(path):
    """Parse JPEG SOF marker to extract (width, height) without decoding pixels.
    Used as a fallback when RTSP probe fails — the zone-drawing thumbnail saved
    at onboard time is the authoritative record of what the camera streamed.
    Returns (None, None) if the file isn't readable or isn't a JPEG.
    """
    try:
        with open(path, "rb") as f:
            data = f.read()
        if data[:2] != b"\xff\xd8":
            return None, None
        i = 2
        while i < len(data) - 9:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            # SOF0..SOF15 except SOF4/8/12 (those are DHT/JPG/DAC)
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                          0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h
            seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
            i += 2 + seg_len
        return None, None
    except Exception:
        return None, None


THUMBNAILS_DIR = "/root/jupyter-hub-controller/media/thumbnails"

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

    def _ensure_ir_auto(self, camera_fc, profiles, ip):
        """Set the camera's IR cut filter to AUTO so plate OCR works at night
        without the user touching anything.

        Why: a meaningful fraction of consumer cameras ship in 'OFF' (forced
        color/day) mode — IR LEDs disabled, plate region clipped at night.
        Setting once at onboard makes the camera's built-in light sensor swap
        the IR cut filter automatically thereafter. Idempotent — safe to re-run.

        Best-effort: cameras with no Imaging service or no IrCutFilter field
        log a debug line and we move on. Never blocks onboard.
        """
        if not profiles:
            return
        try:
            imaging = camera_fc.create_imaging_service()
        except Exception as e:
            logging.debug(f"camera {ip} has no imaging service: {e}")
            return
        try:
            video_source_token = profiles[0].VideoSourceConfiguration.SourceToken
        except Exception as e:
            logging.debug(f"camera {ip} has no VideoSourceConfiguration: {e}")
            return
        try:
            current = imaging.GetImagingSettings({"VideoSourceToken": video_source_token})
            ir_mode = getattr(current, "IrCutFilter", None)
            if ir_mode is None:
                logging.info(f"camera {ip} doesn't expose IrCutFilter — skipping")
                return
            if str(ir_mode).upper() == "AUTO":
                logging.info(f"camera {ip} IR mode already AUTO — leaving alone")
                return
            logging.info(f"camera {ip} IR mode is {ir_mode!r} — flipping to AUTO")
            current.IrCutFilter = "AUTO"
            imaging.SetImagingSettings({
                "VideoSourceToken": video_source_token,
                "ImagingSettings": current,
                "ForcePersistence": True,
            })
            logging.info(f"camera {ip} IR mode now AUTO (night plate OCR enabled)")
        except Exception as e:
            logging.warning(f"camera {ip} IR mode set failed: {e}")

    def _probe_stream_resolution(self, rtsp_url, fallback_image_path=None, timeout=8):
        """Read stream resolution. Two tiers:

        Tier 1 — ffprobe RTSP: authoritative for the current live stream, ignores
            any ONVIF profile lies. May fail with HTTP 429 on cheap cameras whose
            connection slots are already taken by Frigate / go2rtc.
        Tier 2 — JPEG header parse on fallback_image_path: reads the thumbnail
            saved at onboard time. Bullet-proof since the file exists locally,
            but reflects the resolution AT the moment of capture (could be stale
            if the camera was reconfigured later).

        Returns (width, height) or (None, None) if both tiers fail.
        """
        if rtsp_url:
            try:
                result = subprocess.run(
                    ["ffprobe", "-v", "error", "-select_streams", "v:0",
                     "-show_entries", "stream=width,height", "-of", "json",
                     "-rtsp_transport", "tcp", rtsp_url],
                    capture_output=True, text=True, timeout=timeout, check=False,
                )
                if result.returncode == 0 and result.stdout:
                    data = json.loads(result.stdout)
                    streams = data.get("streams", [])
                    if streams and streams[0].get("width"):
                        return streams[0]["width"], streams[0].get("height")
            except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception) as e:
                logging.warning(f"ffprobe failed for {rtsp_url}: {e}")
        # Tier 2: thumbnail JPEG header
        if fallback_image_path and os.path.exists(fallback_image_path):
            w, h = read_jpeg_dimensions(fallback_image_path)
            if w and h:
                logging.info(f"resolution from thumbnail {fallback_image_path}: {w}x{h}")
                return w, h
        return None, None

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

        # Save snapshot for zone drawing before Frigate restarts. Doubles as the
        # Tier-2 fallback for resolution probing below.
        if rtsp_url:
            self._capture_thumbnail(slug_name, rtsp_url)

        # Probe authoritative stream resolutions for both main + sub. ffprobe is
        # cheap (~2s/stream) and runs once at onboard. Falls back to reading the
        # thumbnail JPEG header if ffprobe fails (e.g. camera at 429 connection
        # limit). Fields stay null only if BOTH tiers fail — template then falls
        # through to legacy defaults so we never regress vs pre-Phase-A behavior.
        if rtsp_url:
            thumb_path = os.path.join(THUMBNAILS_DIR, f"{slug_name}.jpg")
            mw, mh = self._probe_stream_resolution(rtsp_url, fallback_image_path=thumb_path)
            if mw and mh:
                kwargs["main_stream_width"] = mw
                kwargs["main_stream_height"] = mh
                logging.info(f"main stream resolution: {mw}x{mh}")
        sub_url_for_probe = kwargs.get("sub_rtsp_url")
        if sub_url_for_probe and sub_url_for_probe != rtsp_url:
            # Sub stream has no dedicated thumbnail; ffprobe-only.
            sw, sh = self._probe_stream_resolution(sub_url_for_probe)
            if sw and sh:
                kwargs["sub_stream_width"] = sw
                kwargs["sub_stream_height"] = sh
                logging.info(f"sub stream resolution: {sw}x{sh}")

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

            # Auto-fix IR cut filter to AUTO so night plate detection works without
            # any user action. Many cheap cameras ship with IrCutFilter='OFF'
            # (forced day mode = no IR LEDs at night) which silently kills LPR
            # accuracy for nighttime arrivals. We flip it to AUTO once at onboard
            # — the camera's built-in light sensor then drives day/night mode
            # correctly forever. Catches & logs any failure so a camera without
            # imaging service support never blocks onboard.
            self._ensure_ir_auto(camera_fc, profiles, ip)

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
        # M2M fields MUST be popped before the setattr() loop — Django
        # rejects setattr on the forward side of a many-to-many.
        loitering_cameras_data = validated_data.pop("loitering_cameras", None)
        # 2026-05-03 — Same treatment for vehicle_recognition_cameras (v162
        # Flutter wizard PATCHes the multi-camera selection). Without this
        # pop, PATCH 500'd on Direct assignment to the forward side of M2M.
        vehicle_recognition_cameras_data = validated_data.pop(
            "vehicle_recognition_cameras", None,
        )

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

        if vehicle_recognition_cameras_data is not None:
            instance.save()
            instance.vehicle_recognition_cameras.set(vehicle_recognition_cameras_data)
            updated_fields.append("vehicle_recognition_cameras")

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

        if (
            "license_vehicle_recognition" in updated_fields
            or "vehicle_recognition_camera" in updated_fields
            or "vehicle_recognition_cameras" in updated_fields
        ):
            # 2026-05-03 — Mirror loitering's pattern: fire the
            # camera_setting_config Celery task whenever EITHER the toggle OR
            # the camera selection (FK or M2M) changes. Without this, a user
            # who PATCHes only the M2M (after license is already true) wouldn't
            # see the AI container's IS_ENABNLED flag flipped — container
            # stays asleep even though the DB + Frigate config are correct.
            camera_names = self._get_vehicle_camera_names(instance)
            self.handle_license_vehicle_recognition(
                instance.license_vehicle_recognition, camera_names
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

    def _get_vehicle_camera_names(self, instance):
        """2026-05-03 — Mirror of _get_loiter_camera_names for VehicleAI.
        M2M is preferred; legacy single-camera FK is the fallback. Returns
        comma-separated slugs (matching the format that the Celery
        camera_setting_config task writes to the AI container's constants.py).
        """
        camera_names = None
        if instance.license_vehicle_recognition:
            m2m_cameras = list(instance.vehicle_recognition_cameras.all())
            if m2m_cameras:
                camera_names = ",".join(c.slug_name for c in m2m_cameras)
            elif instance.vehicle_recognition_camera:
                camera_names = instance.vehicle_recognition_camera.slug_name
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
