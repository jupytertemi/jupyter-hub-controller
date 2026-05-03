"""Phase A v1.6 Halo onboard views.

Three new endpoints + one auth-gated recovery-secret endpoint:

  GET  /api/halo/onboard-payload        — app fetches bonding bundle
  GET  /api/alarms/wait-online          — long-poll until row appears
  POST /api/internal/halo-register      — transfer_server webhook
  GET  /api/alarms/{slug}/recovery-secret — auth-gated keychain recovery

Plus the legacy `POST /api/alarms` create endpoint stays in views.py (kept
for backwards compat, hardened with RFC 9745 deprecation headers).

Design decisions documented in /tmp/halo-onboard-v1.5/PHASE-A-BACKEND-PLAN.md.
"""
import logging
import time

from django.conf import settings
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from alarm.models import AlarmDevice
from alarm.permissions import LocalOnly
from alarm.serializers import AlarmDeviceSerializer
from alarm.services.halo_token import derive_halo_api_token
from alarm.services.pending_onboard import (
    mark_pending,
    is_pending,
    clear_pending,
    get_onboard_started_at,
    health_check as pending_health_check,
)
from alarm.services.wifi_freq import (
    hub_wifi_status,
    is_5ghz,
    ssid_has_2_4ghz_band,
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# 1. GET /api/halo/onboard-payload
# --------------------------------------------------------------------------
class HaloOnboardPayloadView(APIView):
    """Single-call bootstrap. Replaces the legacy /network/wifi-credentials
    for the Halo flow only — camera onboarding still uses the old endpoint.

    Side effect: writes the slug to the pending-onboard registry with TTL
    300 s. The transfer_server webhook checks this before auto-creating
    the AlarmDevice row, so a hostile ESP on the LAN can't be adopted.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        slug = request.query_params.get("slug", "").strip()
        halo_name = request.query_params.get("name", "").strip() or "Halo"

        if not slug:
            return Response(
                {"error": "missing_slug",
                 "message": "?slug=jupyter-alarm-XXXXXX is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Fail fast if Redis is down rather than mid-flow
        if not pending_health_check():
            return Response(
                {"error": "registry_unavailable",
                 "message": "Hub's pending-onboard registry is offline. Wait 60s and retry; the hub is restarting Redis."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        # Hub WiFi check — refined v1.6: the hub's CURRENT band doesn't
        # matter. What matters is whether the same SSID is BROADCASTING
        # on 2.4 GHz somewhere visible — band-steering routers commonly
        # put the hub on 5 GHz and IoT devices on the 2.4 GHz half of
        # the same logical network. Both end up on the same subnet.
        ssid, freq_mhz = hub_wifi_status()
        if not ssid or not freq_mhz:
            return Response(
                {"error": "hub_wifi_unavailable",
                 "message": "Hub is not connected to a WiFi network. Connect the hub to WiFi and retry.",
                 "ssid": ssid,
                 "freq_mhz": freq_mhz},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        # Scan for 2.4 GHz availability of the same SSID
        has_2_4, scanned_freqs = ssid_has_2_4ghz_band(ssid)
        if not has_2_4:
            # Genuine 5-GHz-only network — rare modern WiFi 6E ax-only setups
            return Response(
                {"error": "ssid_no_2_4ghz",
                 "message": (
                     f"Your network '{ssid}' only broadcasts on 5 GHz, but "
                     "Halo needs 2.4 GHz to connect. ESP32-based devices "
                     "(including all Halos) only support 2.4 GHz WiFi."
                 ),
                 "ssid": ssid,
                 "hub_freq_mhz": freq_mhz,
                 "scanned_freqs_mhz": scanned_freqs,
                 "remediation": [
                     "Enable a 2.4 GHz network on your router (sometimes called 'Legacy', 'IoT', or 'Smart Devices')",
                     "Most dual-band routers have 2.4 GHz on by default — check Settings → WiFi → Bands",
                     "If your router is 5 GHz only, add a small 2.4 GHz access point (~$25) as a bridge",
                 ],
                 "help_url": "https://jupyter.com.au/help/halo-2-4ghz"},
                status=status.HTTP_412_PRECONDITION_FAILED,
            )

        # 2.4 GHz IS available on the SSID — proceed regardless of which
        # band the hub itself is on. Log it for observability.
        if is_5ghz(freq_mhz):
            logger.info(
                "halo_onboard_proceeding_hub_on_5ghz ssid=%s hub_freq=%d "
                "scanned_freqs=%s — Halo will join 2.4 GHz of same SSID",
                ssid, freq_mhz, scanned_freqs,
            )

        # Get hub IP from the WiFi iface — used for both audio receiver
        # (port 5555 in /audiosave) AND MQTT broker (firmware-hardcoded 1883).
        try:
            hub_ip = self._hub_primary_ip()
        except Exception as exc:
            logger.exception("hub_ip_lookup_failed")
            return Response(
                {"error": "hub_ip_unavailable",
                 "message": "Hub could not determine its own IP address. Check network setup.",
                 "detail": str(exc)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        # Read hub WiFi credentials inline via nmcli — same approach as
        # the legacy /network/wifi-credentials endpoint, kept self-contained
        # to avoid cross-app coupling.
        try:
            wifi_creds = self._read_hub_wifi_creds()
        except Exception as exc:
            logger.exception("read_hub_wifi_creds_failed")
            return Response(
                {"error": "wifi_creds_unavailable",
                 "message": "Hub could not read its own WiFi credentials. NetworkManager may not be running.",
                 "detail": str(exc)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        # Mint per-Halo HMAC token (stateless — re-derived on validate)
        halo_api_token = derive_halo_api_token(slug)

        # Mark this slug as pending so the webhook handler will accept it
        mark_pending(slug)

        payload = {
            "wifi_ssid":      wifi_creds["ssid"],
            "wifi_password":  wifi_creds["password"],
            "hub_ip":          hub_ip,
            "hub_mdns":        self._hub_mdns(),
            "halo_slug":       slug,
            "halo_name":       halo_name,
            # Audio streaming receiver port — what /audiosave?port= sets in
            # Halo NVS audio_cfg/recv_port. NOT the MQTT broker port (which
            # is firmware-hardcoded to 1883 and not configurable here).
            "audio_port":      5555,
            "halo_api_token":  halo_api_token,
            "api_port":        8000,
            "api_path":        "/api/alarms/version-fw/update",
            "ntp_server":      "pool.ntp.org",
            "timezone":        settings.TIME_ZONE,
        }

        logger.info(
            "halo_onboard_payload_issued slug=%s ssid=%s hub_ip=%s",
            slug, wifi_creds["ssid"], hub_ip,
        )
        return Response(payload, status=status.HTTP_200_OK)

    @staticmethod
    def _hub_primary_ip() -> str:
        """Best-effort hub IP via socket trick — outbound connect to 8.8.8.8
        without sending; OS picks the right local iface."""
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()

    @staticmethod
    def _hub_mdns() -> str:
        """Read /etc/jupyter-hub-id (set by set_up_mdns_v2.sh) for the
        deterministic mDNS hostname. Falls back to `hostname.local`."""
        import socket
        try:
            with open("/etc/jupyter-hub-id", "r") as f:
                return f.read().strip() + ".local"
        except OSError:
            return f"{socket.gethostname()}.local"

    @staticmethod
    def _read_hub_wifi_creds() -> dict:
        """Returns {"ssid": ..., "password": ...} for the hub's currently-
        active WiFi connection. Uses nmcli; raises on no active WiFi."""
        import subprocess
        # Active connection that's wifi
        out = subprocess.check_output(
            ["nmcli", "-t", "-f", "NAME,TYPE,STATE", "connection", "show", "--active"],
            text=True, timeout=2,
        )
        active_wifi_name = None
        for line in out.strip().splitlines():
            parts = line.split(":")
            if len(parts) >= 3 and parts[1] == "802-11-wireless" and parts[2] == "activated":
                active_wifi_name = parts[0]
                break
        if not active_wifi_name:
            raise RuntimeError("no active WiFi connection")

        # SSID
        ssid_out = subprocess.check_output(
            ["nmcli", "-s", "-g", "802-11-wireless.ssid", "connection", "show", active_wifi_name],
            text=True, timeout=2,
        ).strip()
        # PSK (requires --show-secrets / -s flag)
        psk_out = subprocess.check_output(
            ["nmcli", "-s", "-g", "802-11-wireless-security.psk", "connection", "show", active_wifi_name],
            text=True, timeout=2,
        ).strip()
        return {"ssid": ssid_out, "password": psk_out}


# --------------------------------------------------------------------------
# 2. GET /api/alarms/wait-online
# --------------------------------------------------------------------------
class AlarmWaitOnlineView(APIView):
    """Long-polls until `transfer_server_subscriber` has auto-created the
    AlarmDevice row. Replaces the broken POST /api/alarms create flow.

    Holds one Django sync worker for up to 60 s. Acceptable for Phase A —
    onboard runs once per Halo lifetime. Async upgrade in Phase B.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        identity_name = request.query_params.get("identity_name", "").strip()
        timeout = float(request.query_params.get("timeout", "30"))
        if not identity_name:
            return Response(
                {"error": "missing_identity_name"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        timeout = min(timeout, 60.0)
        deadline = time.monotonic() + timeout
        # Build 156 item 2: tighter polling (0.5s → 0.25s) halves the
        # race window where a webhook commit can land between polls.
        poll_interval = 0.25
        # Build 156 item 6: capture pending-marked timestamp so we can
        # report `time_to_register_seconds` to the iPhone client. Read
        # once at request start; the pending key may be cleared by the
        # webhook before we finish polling.
        started_at = get_onboard_started_at(identity_name)

        def _success_response(device):
            time_to_register = None
            if started_at is not None:
                time_to_register = round(time.time() - started_at, 2)
            return Response(
                {"status": "online",
                 "device": AlarmDeviceSerializer(device).data,
                 "time_to_register_seconds": time_to_register},
                status=status.HTTP_200_OK,
            )

        while time.monotonic() < deadline:
            try:
                device = AlarmDevice.objects.get(identity_name=identity_name)
                return _success_response(device)
            except AlarmDevice.DoesNotExist:
                time.sleep(poll_interval)

        # Final-grace check: catches commits that landed between the last
        # poll and the deadline. The webhook auto-create transaction can
        # be slow when HA registration runs inline (2-5s), so a register
        # heartbeat at T=28 might commit at T=30.5 — outside our polling
        # window. Grace check closes that race without extending the
        # wall-clock budget.
        try:
            device = AlarmDevice.objects.get(identity_name=identity_name)
            logger.info(
                "halo_wait_online_grace_hit identity_name=%s waited=%.1fs",
                identity_name, timeout,
            )
            return _success_response(device)
        except AlarmDevice.DoesNotExist:
            pass

        logger.warning(
            "halo_wait_online_timeout identity_name=%s waited=%.1fs",
            identity_name, timeout,
        )
        # Build 156 item 6: surface timing context on 408 so the iPhone's
        # late-register fallback (Build 156 item 1) can decide whether to
        # immediately fire `GET /alarms?identity_name=` (the row may have
        # JUST landed in the milliseconds between our last poll and this
        # response).
        from datetime import datetime, timezone as _tz
        last_register_check_at = datetime.now(_tz.utc).isoformat().replace(
            "+00:00", "Z",
        )
        return Response(
            {"status": "timeout",
             "error": "halo_did_not_register",
             "message": (
                 "Halo did not register within the timeout window. Common causes: "
                 "WiFi credentials wrong, Halo couldn't reach hub MQTT broker, "
                 "or Halo firmware crashed during boot. Restart the Halo and retry."
             ),
             "identity_name": identity_name,
             "time_to_register_seconds": None,
             "last_register_check_at": last_register_check_at,
             "onboard_started_at_known": started_at is not None},
            status=status.HTTP_408_REQUEST_TIMEOUT,
        )


# --------------------------------------------------------------------------
# 3. POST /api/internal/halo-register  (transfer_server webhook)
# --------------------------------------------------------------------------
class HaloRegisterWebhookView(APIView):
    """Internal — only reachable from docker bridge / localhost.

    Single source of truth for AlarmDevice row creation. transfer_server
    POSTs the register payload (with peer_ip from the TCP socket) on
    every successful ESP register event. This handler:

      1. Validates payload + role
      2. Checks pending-onboard registry — REJECTS unauthorized slugs
      3. Writes minimum row to AlarmDevice (FAST PATH, no HTTP queries)
      4. Returns 200 to transfer_server immediately
      5. Off-thread Celery task enriches with /api/status data and
         publishes HA Auto-Discovery
    """

    permission_classes = [LocalOnly]
    authentication_classes = []  # internal endpoint, network-restricted

    def post(self, request):
        payload = request.data or {}

        # Silent ignore for non-ESP registers (transfer_server may relay
        # other roles in future)
        if payload.get("role") != "esp":
            return Response(status=status.HTTP_204_NO_CONTENT)

        slug = (payload.get("device") or "").strip()
        peer_ip = (payload.get("peer_ip") or "").strip()
        device_secret = (payload.get("device_secret") or "").strip()

        if not slug:
            return Response(
                {"error": "missing_device"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not peer_ip:
            return Response(
                {"error": "missing_peer_ip",
                 "message": "transfer_server webhook must include peer_ip"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Authorization gate — slug must be in the pending set. Hostile ESPs
        # on the LAN that just open a socket and send a register payload get
        # rejected here, no DB mutation.
        #
        # Distinguish two not-pending cases:
        #   (a) slug already corresponds to an onboarded Halo — that's the
        #       2-min TCP heartbeat from a known device, not a hostile rule.
        #       Return 204 silently. No log spam.
        #   (b) slug is unknown — actual hostile-LAN reject. Log + 403.
        if not is_pending(slug):
            if AlarmDevice.objects.filter(identity_name=slug).exists():
                # Heartbeat from already-onboarded Halo. Acknowledged silently.
                return Response(status=status.HTTP_204_NO_CONTENT)
            logger.warning(
                "halo_register_rejected_not_pending slug=%s peer_ip=%s",
                slug, peer_ip,
            )
            return Response(
                {"status": "rejected", "reason": "not_pending"},
                status=status.HTTP_403_FORBIDDEN,
            )

        # FAST PATH — write minimum row, return immediately.
        # Enrichment (mac, fw, name from /api/status) is off-thread.
        from django.db import transaction
        from alarm.services.halo_enrichment import derive_mac_from_slug

        defaults = {
            "name": slug,  # placeholder; Celery task will overwrite from /api/status
            "type": self._infer_type(slug),
            "ip_address": peer_ip,
            "hass_entry_id": slug,
            "device_secret": device_secret,
            "mac_address": derive_mac_from_slug(slug),
        }

        with transaction.atomic():
            device, created = AlarmDevice.objects.select_for_update().update_or_create(
                identity_name=slug,
                defaults=defaults,
            )

        if created:
            clear_pending(slug)
            logger.info(
                "halo_alarm_device_created id=%s slug=%s peer_ip=%s",
                device.id, slug, peer_ip,
            )

        # Off-thread enrichment + HA discovery
        from alarm.tasks import enrich_and_publish_ha_discovery
        enrich_and_publish_ha_discovery.delay(device.id)

        return Response(
            {"status": "ok", "created": created, "id": device.id},
            status=status.HTTP_200_OK,
        )

    @staticmethod
    def _infer_type(slug: str) -> str:
        """Currently slugs don't encode the type (they're all jupyter-alarm-X).
        The serial DOES (JUP-OUTDR-X vs JUP-INDR-X) but that's not in the
        register payload. We could enrich from /api/status (returns 'serial')
        but for the minimum row we default to INDOOR; the Celery task
        upgrades it after fetching /api/status."""
        from alarm.enums import AlarmType
        return AlarmType.INDOOR


# --------------------------------------------------------------------------
# 4. GET /api/alarms/{slug}/recovery-secret  (auth-gated keychain recovery)
# --------------------------------------------------------------------------
class HaloRecoverySecretView(APIView):
    """Returns the device_secret for a registered Halo. Used when the user
    installs the app on a new phone and lost the keychain entry.

    NOT exposed in the default AlarmDeviceSerializer — must use this
    explicit endpoint, which requires hub auth.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, slug: str):
        try:
            device = AlarmDevice.objects.get(identity_name=slug)
        except AlarmDevice.DoesNotExist:
            return Response(
                {"error": "not_found",
                 "message": "No Halo with this identity_name is registered to this hub."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not device.device_secret:
            return Response(
                {"error": "secret_not_stored",
                 "message": (
                     "This Halo was onboarded before v1.6 — the hub doesn't "
                     "have its device_secret stored. Use the iPhone keychain "
                     "entry, or factory-reset the Halo and re-onboard to "
                     "store the secret."
                 ),
                 "identity_name": slug},
                status=status.HTTP_404_NOT_FOUND,
            )

        logger.info("halo_recovery_secret_fetched slug=%s by=%s", slug, request.user)
        return Response(
            {"identity_name": slug,
             "device_secret": device.device_secret,
             "warning": "Sensitive — store in keychain, never log or transmit."},
            status=status.HTTP_200_OK,
        )
