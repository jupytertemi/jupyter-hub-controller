# Halo Onboarding — Phase A Backend Plan v1.6

**For:** Flutter dev (Long) + backend dev (Pha / Chanh) + firmware team alignment
**Status:** v1.6, awaiting greenlight to code
**Goal:** Cut p50 onboard from 30-55s → ≤30s, eliminate the "POST /alarms 400"
failure mode, surface specific error reasons to the user, **without exposing
hub-wide credentials over the air or auto-adopting hostile devices on the LAN**.
**No firmware change, no BLE work.** Backwards-compatible with apps in the field.

---

## Changelog from v1.0

| # | v1.0 | v1.6 |
|---|------|------|
| 1 | `api_key: <hub_secret>` shipped over plaintext WiFi | **Per-Halo HMAC-derived token** (`HMAC-SHA256(hub_secret, halo_slug)`); hub_secret never leaves the hub |
| 2 | `_resolve_halo_ip(slug)` via mDNS | **`peer_ip` carried in transfer_server webhook payload** — deterministic, no mDNS dependency |
| 3 | HA discovery published only on `created=True` | **Republished on `mac/fw/ip` change** — single retry race no longer leaves HA in a permanent broken state |
| 4 | `audio_port: 1883` (was conflicting with bloc's 5555) | **`mqtt_port: 1883`** explicit; `audio_port` flagged as Q for firmware |
| 5 | Auto-create AlarmDevice on any ESP register | **PendingHaloOnboard registry** (Redis, 5-min TTL); webhook only creates if slug is in pending set |
| 6 | 5 GHz heuristic on SSID name | **`iw dev` frequency query** — deterministic for band-steering routers |
| 7 | `/api/status` mac as primary | **`/api/status` with `/api/device_info` fallback** — no firmware-team blocker |
| 8 | Deprecation in code comments only | **RFC 9745 `Deprecation: true` + `Sunset: <date>` headers** + Prometheus counter |
| 9 | (covered in #3) | (covered in #3) |
| 10 | `AlarmDeviceSecret` separate table | **`device_secret` column on `AlarmDevice`** — YAGNI on separate table |

---

## Threat model (new — addresses #1 and #5)

**Attackers we care about:**

1. **Sniffer in RF range during onboarding.** The Halo's WPA AP password is on
   the QR label, glued to the device — physical access to a unit defeats it.
   Anyone who can sniff the captive WiFi exchange between iPhone and Halo can
   capture every byte the app sends.
2. **Hostile ESP on the same LAN.** Port 4444 is reachable from any device on
   the local subnet. A neighbour's Halo, a retail demo unit, or an attacker's
   ESP could send a `register` payload.
3. **Compromised Halo.** Adversary recovers a Halo's NVS contents (e.g.
   physically tearing one apart). Anything stored there is in their hands.

**What we will NOT accept as collateral:**

- Hub-wide credential exposed in any of the above scenarios.
- Hostile device adopted into a customer's hub without their action.
- Permanent corruption of the `AlarmDevice` table or HA entity state.

**What we accept as expected loss:**

- Loss of one Halo's MQTT/socket access if its NVS is exfiltrated. (Per-Halo
  scoping; rotation on factory-reset.)
- Brief window (≤5 min, TTL of pending-onboard) during which a hostile ESP
  could race a legitimate register with a known slug. The slug is in the QR
  on the Halo; an attacker who has the QR is already past physical security.

---

## Endpoint contracts

### 1. `GET /api/halo/onboard-payload`

Single-call bootstrap. Replaces the existing `/network/wifi-credentials` for
the Halo onboard flow only (the old endpoint stays for camera onboarding).

**Side effect (new in v1.6):** writes `pending_onboard:{slug}` to Redis with
TTL 300 s. The webhook handler in §3 uses this as an authorization check.

```
GET /api/halo/onboard-payload?slug=jupyter-alarm-eaa324&name=Front%20Door
Authorization: Basic <hub_basic_auth>
```

| Query param | Required | Notes |
|---|---|---|
| `slug` | yes | the SSID/identity from the QR (e.g. `jupyter-alarm-eaa324`) |
| `name` | optional | user-given Halo name; backend stores it for the eventual `AlarmDevice.name` |

**Response 200:**
```json
{
  "wifi_ssid":     "blueberry_google",
  "wifi_password": "<plaintext>",
  "hub_ip":        "192.168.1.225",
  "hub_mdns":      "radxa-a8e2914384be.local",
  "halo_slug":     "jupyter-alarm-eaa324",
  "halo_name":     "Front Door",
  "mqtt_port":     1883,
  "halo_api_token": "9f4a8e2bd7c3a1f6...",
  "api_port":      80,
  "api_path":      "/api/alarms/version-fw/update",
  "ntp_server":    "pool.ntp.org",
  "timezone":      "Australia/Melbourne"
}
```

`halo_api_token` is the **per-Halo HMAC-derived token** the app passes to the
Halo's `/audiosave` endpoint (replacing the legacy `api_key=<hub_secret>`).

```python
halo_api_token = hmac.new(
    key=settings.HUB_SECRET.encode(),
    msg=f"halo:{halo_slug}".encode(),
    digestmod=hashlib.sha256,
).hexdigest()
```

The hub re-derives the token to validate any Halo→hub call carrying it; the
token is **stateless** on the hub side (no DB lookup needed). If a Halo's
NVS is exfiltrated, only that Halo's token is burnt — it can be invalidated
by changing the Halo's slug or rotating `HUB_SECRET` (which rotates every
hub's tokens at once, used as a nuclear option).

**Response 412 — hub on 5 GHz (deterministic, replaces SSID-name heuristic):**

Implementation queries `iw dev <wifi-iface> link` to get the actual frequency:

```python
def _hub_wifi_freq_mhz() -> int:
    out = subprocess.check_output(["iw", "dev", iface, "link"], text=True)
    m = re.search(r"freq:\s*(\d+)", out)
    return int(m.group(1)) if m else 0

# 5 GHz = 5180-5825, 2.4 GHz = 2412-2484
def _is_5ghz(freq_mhz: int) -> bool:
    return freq_mhz >= 5000
```

```json
{
  "error":   "hub_on_5ghz",
  "message": "Hub is on a 5 GHz network ('blueberry_google'). Halo only supports 2.4 GHz. Connect the hub to a 2.4 GHz network and retry.",
  "ssid":    "blueberry_google",
  "freq_mhz": 5180
}
```

**Response 503 — hub WiFi unavailable:**
```json
{
  "error":   "hub_wifi_unavailable",
  "message": "Hub is not connected to a WiFi network. Connect the hub to 2.4 GHz WiFi and retry.",
  "detail":  "<exception text>"
}
```

**Latency target:** <200 ms (cached config + one `iw dev` shell call).

---

### 2. `GET /api/alarms/wait-online`

Long-polls until `transfer_server_subscriber` has auto-created the `AlarmDevice`
row from the Halo's TCP register on port 4444.

```
GET /api/alarms/wait-online?identity_name=jupyter-alarm-eaa324&timeout=30
Authorization: Basic <hub_basic_auth>
```

| Query param | Required | Default | Max |
|---|---|---|---|
| `identity_name` | yes | — | — |
| `timeout` | no | 30 | 60 |

**Response 200 — Halo registered:**
```json
{
  "status": "online",
  "device": {
    "id":             1,
    "name":           "Front Door",
    "identity_name":  "jupyter-alarm-eaa324",
    "type":           "INDOOR",
    "version_fw":     "2.21.0",
    "ip_address":     "192.168.1.222",
    "mac_address":    "ac:a7:04:ea:a3:24",
    "hass_entry_id":  "jupyter-alarm-eaa324",
    "created_at":     "2026-05-02T12:34:56Z",
    "updated_at":     "2026-05-02T12:34:56Z"
  }
}
```

**Response 408 — Halo did not register:**
```json
{
  "status":         "timeout",
  "error":          "halo_did_not_register",
  "message":        "Halo did not register within the timeout window. Common causes: WiFi credentials wrong, Halo couldn't reach hub MQTT broker, or Halo firmware crashed during boot. Restart the Halo and retry.",
  "identity_name":  "jupyter-alarm-eaa324"
}
```

**Worker-pool note (#15):** Each call holds one Django sync worker for up to
60 s. Acceptable for Phase A — onboard happens once per Halo lifetime, single
customer setting up multiple Halos one at a time. **Documented constraint;
upgrade to Channels async handler in Phase B.**

---

### 3. `POST /api/internal/halo-register` (transfer_server webhook)

**Internal-only.** HAProxy ACL restricts `/api/internal/*` to the docker
bridge network. Public hits get 404.

The transfer_server container is patched to POST every successful ESP
`register` event to this URL. **Critical: the patch includes `peer_ip`** —
the source IP of the TCP socket, which transfer_server already has.

**Webhook request:**
```json
{
  "action":         "register",
  "role":           "esp",
  "device":         "jupyter-alarm-eaa324",
  "device_secret":  "<64-hex>",
  "s1_count":       0,
  "k11_count":      0,
  "peer_ip":        "192.168.1.222"
}
```

**Handler logic:**

The handler is **deliberately fast** — it writes the minimum row and returns
200 to transfer_server immediately. The enrichment HTTP queries to the Halo
(which can take up to 6-9s with retries) are pushed to a Celery task
(`enrich_pending_alarm`) that runs off the request hot path. This avoids
transfer_server's HTTP client timing out and dropping the register signal
(per Flutter dev review obs #1).

```python
class HaloRegisterWebhookView(APIView):
    """Internal — only reachable from docker bridge."""
    permission_classes = [LocalOnly]

    def post(self, request):
        payload = request.data
        if payload.get("role") != "esp":
            return Response(status=204)

        slug = payload["device"]
        peer_ip = payload.get("peer_ip")
        if not peer_ip:
            return Response(
                {"error": "missing_peer_ip"}, status=400
            )

        # Authorization gate (#5): slug must be in the pending set
        if not redis.exists(f"pending_onboard:{slug}"):
            logger.warning(
                "halo_register_rejected_not_pending slug=%s peer_ip=%s",
                slug, peer_ip,
            )
            return Response({"status": "rejected", "reason": "not_pending"}, status=403)

        # FAST PATH — write minimum row, return 200 to transfer_server.
        # No HTTP queries to Halo here; that happens off-thread.
        defaults = {
            "name":          slug,
            "type":          self._infer_type(slug),
            "ip_address":    peer_ip,
            "hass_entry_id": slug,
            "device_secret": payload.get("device_secret", ""),
        }

        with transaction.atomic():
            device, created = AlarmDevice.objects.select_for_update().update_or_create(
                identity_name=slug,
                defaults=defaults,
            )

        # Pending-onboard cleared on first successful create
        if created:
            redis.delete(f"pending_onboard:{slug}")

        # Off-thread: enrich (mac, fw, name) + publish HA discovery
        # Celery task is idempotent on retransmits; safe if heartbeat
        # triggers another fast-path write while it's running.
        enrich_and_publish_ha_discovery.delay(device.id)

        return Response({
            "status":  "ok",
            "created": created,
            "id":      device.id,
        })
```

The `enrich_and_publish_ha_discovery` Celery task does the `/api/status` +
`/api/device_info` queries, retries up to 10× with exponential backoff,
updates the row, and republishes HA discovery on any field change.

```python
@shared_task(bind=True, max_retries=10, default_retry_delay=60)
def enrich_and_publish_ha_discovery(self, device_id: int):
    device = AlarmDevice.objects.get(id=device_id)
    needs_enrichment = not device.mac_address or not device.version_fw

    if needs_enrichment:
        enrichment = _enrich_from_halo(device.ip_address)
        fields_changed = []
        if enrichment.get("mac_address") and not device.mac_address:
            device.mac_address = enrichment["mac_address"]
            fields_changed.append("mac_address")
        if enrichment.get("fw_version") and not device.version_fw:
            device.version_fw = enrichment["fw_version"]
            fields_changed.append("version_fw")
        if enrichment.get("name") and device.name == device.identity_name:
            device.name = enrichment["name"]
            fields_changed.append("name")
        if fields_changed:
            device.save(update_fields=fields_changed)

        if not device.mac_address or not device.version_fw:
            # Still missing — try again in 60s, up to 10 retries
            raise self.retry()

    # Publish HA discovery if any tracked field changed
    publish_ha_discovery_if_needed(device)


def _enrich_from_halo(peer_ip: str) -> dict:
    """Try /api/status first, fallback to /api/device_info. 3× retry within
    a single task invocation; the outer Celery retry is a separate loop."""
    for attempt in range(3):
        try:
            r = requests.get(f"http://{peer_ip}/api/status", timeout=2)
            if r.status_code == 200:
                s = r.json()
                return {
                    "name":        s.get("device"),
                    "fw_version":  s.get("firmware"),
                    "mac_address": s.get("mac_address") or _fallback_mac(peer_ip),
                }
        except Exception:
            pass
        time.sleep(1)
    return {"mac_address": _fallback_mac(peer_ip)}


def _fallback_mac(peer_ip: str) -> str:
    try:
        r = requests.get(f"http://{peer_ip}/api/device_info", timeout=2)
        if r.status_code == 200:
            return r.json().get("mac", "").lower()
    except Exception:
        pass
    return ""
```

**Idempotency:** transfer_server sends the register payload on initial
connect AND on every 30 s heartbeat. The handler must not corrupt or spam
the DB. `update_or_create` with `select_for_update` covers this; the
`pending_onboard` key is deleted on first successful create so subsequent
heartbeats hit the not_pending guard and 403.

This is **safe by design**: the second-onwards heartbeat returning 403 is
expected, the slug is already in the DB, no further action needed.

---

### 4. HA Auto-Discovery republish on field change (Celery task)

```python
@shared_task
def publish_ha_discovery_if_needed(device_id: int):
    device = AlarmDevice.objects.get(id=device_id)
    last = HaDiscoveryState.objects.filter(device=device).first()

    fingerprint = (
        device.mac_address,
        device.version_fw,
        device.ip_address,
        device.name,
    )
    if last and last.fingerprint == fingerprint:
        return  # nothing changed, no need to republish

    payload = {
        "name":          device.name,
        "unique_id":     device.identity_name,
        "command_topic": f"/{device.identity_name}/mode",
        "state_topic":   f"/{device.identity_name}/status",
        "device": {
            "identifiers":  [device.identity_name],
            "manufacturer": "Jupyter",
            "model":        f"Halo-{device.type}",
            "sw_version":   device.version_fw,
            "connections":  [["mac", device.mac_address]] if device.mac_address else [],
        },
    }
    topic = f"homeassistant/alarm_control_panel/{device.identity_name}/config"
    mqtt_client.publish(topic, json.dumps(payload), qos=1, retain=True)

    HaDiscoveryState.objects.update_or_create(
        device=device, defaults={"fingerprint": fingerprint},
    )
```

`HaDiscoveryState` is a one-row-per-device tracking table so we don't republish
on every register heartbeat (race-safe with the `select_for_update` in #3).

---

### 5. `enrich_pending_alarm` Celery task (belt-and-braces)

```python
@shared_task(bind=True, max_retries=10, default_retry_delay=60)
def enrich_pending_alarm(self, device_id: int):
    device = AlarmDevice.objects.get(id=device_id)
    if device.mac_address and device.version_fw:
        return  # already enriched

    enrichment = _enrich_from_halo(device.ip_address)
    fields_changed = []
    if enrichment.get("mac_address") and not device.mac_address:
        device.mac_address = enrichment["mac_address"]
        fields_changed.append("mac_address")
    if enrichment.get("fw_version") and not device.version_fw:
        device.version_fw = enrichment["fw_version"]
        fields_changed.append("version_fw")
    if fields_changed:
        device.save(update_fields=fields_changed)
        publish_ha_discovery_if_needed.delay(device.id)
        return

    # Still missing — retry
    raise self.retry()
```

Bounds the retry loop. After 10 retries × 60 s = ~10 min of trying, gives up.
Operator can manually invoke via Django shell.

---

### 6. `POST /api/alarms` — kept for backwards compat, deprecated via headers (#8)

```python
class ListCreateAlarmDeviceView(ListCreateAPIView):
    def create(self, request, *args, **kwargs):
        # Mark via response headers per RFC 9745
        response = super().create(request, *args, **kwargs)
        response["Deprecation"] = "true"
        response["Sunset"] = "Sat, 30 Nov 2026 00:00:00 GMT"
        response["Link"] = '</api/halo/onboard-payload>; rel="successor-version"'
        legacy_alarm_create_counter.inc()  # Prometheus
        return response
```

When the counter hits zero for >2 weeks across all hubs, safe to delete the
endpoint entirely (Phase C cleanup).

Hardened error messages: when validation fails, return specific reason in
`detail` field rather than generic "An error occurred".

---

## Files I'll create / modify

### New files

| Path | Purpose | LOC |
|---|---|---|
| `apps/alarm/views_halo_onboard.py` | `HaloOnboardPayloadView`, `AlarmWaitOnlineView`, `HaloRegisterWebhookView` | ~350 |
| `apps/alarm/services/halo_token.py` | HMAC token derive + verify | ~40 |
| `apps/alarm/services/pending_onboard.py` | Redis-backed pending-onboard registry | ~50 |
| `apps/alarm/services/halo_enrichment.py` | `/api/status` + `/api/device_info` query, retry logic | ~80 |
| `apps/alarm/services/wifi_freq.py` | `iw dev` query for hub WiFi frequency | ~30 |
| `apps/alarm/tasks.py` | `publish_ha_discovery_if_needed`, `enrich_pending_alarm` Celery tasks | ~80 |
| `apps/alarm/migrations/0024_halo_onboard_v1_6.py` | Add `device_secret` column on `AlarmDevice`; new `HaDiscoveryState` table | auto |
| `apps/alarm/tests/test_halo_onboard.py` | Unit + integration coverage | ~350 |
| `apps/alarm/tests/test_halo_security.py` | HMAC token, pending-onboard auth gate, deprecation headers | ~150 |

### Modified files

| Path | Change | LOC |
|---|---|---|
| `apps/alarm/urls.py` | Wire 3 new routes | +6 |
| `apps/alarm/models.py` | `device_secret` col, `HaDiscoveryState` model | +30 |
| `apps/alarm/serializers.py` | Hardened 400 responses | +30 |
| `apps/alarm/views.py` | RFC 9745 deprecation headers + counter | +15 |
| `apps/alarm/apps.py` | Register signal handler (kept minimal) | +3 |
| `transfer_server/<file>.py` | Webhook on register event with peer_ip | ~40 |

### No changes

- AlarmDevice schema beyond `device_secret` column
- `/api/network/wifi-credentials` (camera onboarding still uses it)
- `/api/alarms` GET/PUT/DELETE
- HA, MQTT broker, EMQX config

---

## Test plan (#11)

### Unit tests — happy path

- `HaloOnboardPayloadView` returns 200 with all fields populated when hub on 2.4 GHz.
- HMAC token deterministic for same (slug, hub_secret); different for different slugs.
- `iw dev` 2.4 GHz freq → 200; 5 GHz freq → 412 with `freq_mhz` field.
- `AlarmWaitOnlineView` returns 200 within 1 s when row pre-exists.
- Webhook with `role=esp` + slug in pending → upsert, 200.

### Unit tests — negative path (mandatory per #11)

- Webhook with malformed JSON → 400.
- Webhook with `role!=esp` → 204 (silent ignore).
- Webhook with slug NOT in pending → 403, no DB row created, no HA publish.
- Webhook with missing `peer_ip` → 400.
- Two concurrent webhooks for same slug → exactly one row created. **Use real `threading.Thread`** (not `pytest-asyncio`) because the SQL row-lock timing isn't exercised by async tests (per Flutter dev review obs #3). Test must spawn 2+ OS threads, each opening their own DB connection, both calling the webhook simultaneously, then assert exactly one `AlarmDevice` row with `identity_name=slug` exists at the end.
- App calls `wait-online` for slug never registered → 408, clean response.
- App calls `wait-online` AFTER 408 retry, Halo registered between calls → 200 immediately.
- `/api/status` returns connection-refused on first call, succeeds on second → row enriched correctly.
- `/api/status` returns 200 but missing `mac_address` → falls through to `/api/device_info`.
- `enrich_pending_alarm` retries until limit, then logs and stops (no infinite loop).
- Deprecated `POST /api/alarms` returns 200 + `Deprecation: true` header + Prometheus counter incremented.

### Integration test — live hub end-to-end

Scan QR on a fresh-NVS Halo → app completes onboard → `AlarmDevice` row created
with mac, fw, ip populated → HA shows Halo as alarm_control_panel entity →
Halo's `/{slug}/mode` MQTT topic responds to commands → app dashboard shows
Halo card.

**Pass criteria:** ≤30 s p50 onboard, ≤60 s p95, zero 400 errors across
10 retries, zero "No Internet" hard-fail false positives.

### Flutter empirical UX tests (#12)

**Test A — current iOS UI inventory (BEFORE any code change).** Long
screen-records a complete onboard on the current production build, documents
every iOS surface that appears (banner, captive sheet, system prompt). The
rationale screen text is written AGAINST that record, not speculation.

**Test B — `isHidden: true` removal A/B (BEFORE locking).**
1. Build A: current code, `isHidden: true`. Onboard, screen-record.
2. Build B: patched, `isHidden: false`. Onboard same Halo (NVS cleared between
   attempts), screen-record. Run on iOS current + iOS 17.x if available.

**Decision criteria — locked NOW to avoid Sunday-morning debate:**
- If captive sheet appears in Build B but not A → revert `isHidden` removal,
  3-5 s of join time worth keeping clean UX.
- If both builds same → keep removal, banner-only rationale text.
- If Build A had captive sheet too → both fail equally; rationale text covers
  it. No revert.

---

## Saturday go/no-go (#14)

### Pass criteria (all must hold to proceed with v1.6 in pilot)

1. Backend unit tests pass on feature branch.
2. Backend deployed to Mill-Valley; live onboard test of the actual Halo
   (`jupyter-alarm-eaa324`) completes successfully ≥3 times consecutively
   with ≤30 s p50.
3. HA shows the Halo as an entity (`alarm_control_panel.front_door` or similar).
4. MQTT round-trip: app sends mode command, Halo's LED changes, status pub
   reflects state.
5. Concurrent register heartbeat doesn't create duplicate rows (run test for
   ≥2 minutes after onboard).
6. Long sign-off on Flutter side: build runs, both empirical tests A and B
   recorded, decision made on `isHidden`.
7. Pha or Chanh sign-off on backend security review (HMAC token + pending-onboard
   gate specifically).

### Fallback if any criterion fails

Pilot uses **v151 captive-portal flow** — slow but functional. v1.6 ships in
the next sprint instead. **Do not pilot a half-validated stack.**

This is a hard line: better a slow demo than a public failure on national TV
at the JB HiFi launch.

---

## Migration / deployment plan

1. Author backend on feature branch `feat/halo-onboard-v1.6`.
2. Migration `0024_halo_onboard_v1_6` reviewed for `device_secret` column +
   `HaDiscoveryState` table. Reversible.
3. Pha or Chanh **mandatory review** of:
   - HMAC token derivation (`apps/alarm/services/halo_token.py`)
   - Pending-onboard gate (`apps/alarm/views_halo_onboard.py:HaloRegisterWebhookView`)
   - transfer_server webhook patch
4. Deploy to Mill-Valley. Run go/no-go test plan above.
5. Long writes Flutter side against this contract in parallel — does NOT
   ship until backend is signed off + Mill-Valley validated.
6. Re-snapshot Mill-Valley AFTER both sides validated. That's the gold image
   for pilot.

---

## Open questions for Flutter / firmware

1. **Flutter (Long):** Confirm new endpoint shape doesn't break v151 callers.
   Contract is a superset of `/network/wifi-credentials`, but please verify.
2. **Flutter (Long):** Empirical iOS UI inventory (test A above) before
   locking rationale screen copy.
3. **Firmware:** Confirm `transfer_server` can have an outbound HTTP webhook
   on register events. If not, we need a small (~30 line) patch — and it must
   include `peer_ip` from the TCP socket.
4. **Firmware:** What does `/audiosave?port=X` actually configure — MQTT broker
   port (the trace doc shows `mqtt::set_broker_port(port)` so 1883), or a
   separate audio-relay port (the bloc has been sending 5555)? Pin one
   answer; if both needed, two fields not one.
5. **Firmware:** Confirm Halo's `/api/status` returns `mac_address` or — if
   not — confirm `/api/device_info` does. Don't block on this; v1.6 codes
   defensively with both as fallbacks.
6. **Firmware:** Halo's existing API endpoint `/api/alarms/version-fw/update`
   (referenced in `audiosave?api_path=...`) — confirm it exists and what it
   accepts. If new, needs its own design section. **Per Flutter dev review
   obs #2: this is a forward reference in the onboard-payload contract —
   if the endpoint doesn't exist, Halo will silently 404 on firmware update
   attempts. Either confirm + document its contract, OR explicitly mark
   the field as Phase A.5 deliverable so we don't ship mysterious
   "firmware update" support tickets.**
7. **Operator:** Saturday rehearsal at JB HiFi venue. Who can do it? Backup
   plan if venue access not possible.
8. **Operator:** Who reviews backend security changes (Pha or Chanh) — and
   when can they make 1-2 hours available Saturday?

---

## What we're explicitly NOT doing in Phase A

- BLE-based onboarding (Phase B, post-pilot, ~3 weeks)
- WebSocket signaling (Phase B alongside BLE)
- NEHotspotConfiguration on iOS (deferred — needs Apple entitlement)
- Replacing existing `POST /api/alarms` (kept for backwards compat with
  RFC 9745 deprecation headers)
- HA Automations (occupancy illusion, alarm modes — out of scope per Temi)
- Service-discovery via Avahi `_jupyter._tcp` (firmware does hostname lookup,
  not service browse — would be wasted code without a firmware update)
- Async Channels handler for `wait-online` (Phase B; sync handler documented
  as constraint for Phase A)

---

## Estimated effort (revised v1.6)

| Phase | Hours | Owner |
|---|---|---|
| Backend code + unit tests + security tests | 9-12h | Backend (Pha / Chanh) |
| Backend security review (mandatory) | 1-2h | 2nd reviewer |
| Mill-Valley deploy + live validation | 2h | Backend + operator |
| Flutter code + tests | 6-8h | Long |
| Flutter empirical UX tests (A and B) | 1h | Long |
| Flutter UI rationale screen | 1h | Long / designer |
| Saturday venue rehearsal | 2h | Operator |
| **Saturday evening go/no-go** | 1h | All |
| **Total team-hours** | **22-28h** | — |

Calendar: backend Friday morning - Saturday morning, Flutter Friday-Saturday
parallel, integration Saturday afternoon, **go/no-go Saturday evening**,
snapshot Sunday morning, pilot Sunday.

---

## Security review checklist (for Pha / Chanh)

Specifically for `apps/alarm/views_halo_onboard.py:HaloRegisterWebhookView`
and `apps/alarm/services/halo_token.py`:

- [ ] HMAC key (`HUB_SECRET`) never logged.
- [ ] `halo_api_token` returned only via authenticated `GET /api/halo/onboard-payload`,
      never exposed in logs or any other endpoint.
- [ ] Pending-onboard registry (Redis) has TTL configured and verified — no
      slug stays pending forever if app crashes mid-onboard.
- [ ] Webhook handler rejects all non-localhost / non-bridge requests (HAProxy
      ACL + `LocalOnly` permission class). Test from external IP returns 404
      or 403.
- [ ] Webhook handler is idempotent on heartbeat retransmits (transfer_server
      sends register every 30 s).
- [ ] `select_for_update` prevents concurrent-register race on same slug.
- [ ] `device_secret` column is NOT included in any default serializer
      response (write-only, internal use).
- [ ] HMAC token derivation is documented + tested for output stability across
      Python versions.
- [ ] `enrich_pending_alarm` Celery task has bounded retry + logs giving up.
- [ ] `iw dev` shell-out is sanitized; doesn't accept user input.

---

## Closing notes

The five critical items from the v1.0 review (hub_secret leak, mDNS hand-wave,
HA discovery race, audio_port confusion, auto-create privacy hole) are all
addressed in v1.6 above. The five important items are folded in. The five
nice-to-haves are tested or documented.

This plan is reviewable by a security-minded engineer in ~45 minutes and
implementable by Pha / Chanh in 9-12 hours. The Flutter side is unchanged in
scope from v1.0, just consumes the new token field.

If anything in v1.6 still raises a flag, please push back BEFORE coding starts
on Friday morning. The Sunday pilot is much better served by an extra hour of
plan refinement today than by a Saturday-night fire-drill rollback.
