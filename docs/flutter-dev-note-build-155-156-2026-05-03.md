# Note for Flutter dev — Build 155 status + Build 156 reliability items

**Date**: 2026-05-03
**From**: backend dev (after the late-night reliability hunt)

## Build 155 — what we need

| Item | Status |
|---|---|
| `isHidden: true` revert in `halo_wifi_adapter.dart:50` | ✅ committed (80d909d2) |
| `pubspec.yaml` 1.0.1+155 | ✅ |
| Watch CFBundleVersion 155 | ✅ |
| **IPA build + TestFlight upload** | **⏸ pending — please prioritize** |

Tonight we proved twice that iPhone WiFi cold-scan to the Halo softAP is unreliable on Build 154 — user had to restart the app to recover. Build 155's `isHidden: true` is the fix. The TestFlight upload is what closes the loop.

## Build 156 reliability items (not 155 blockers, ship together)

### 1. Defensive `wait-online` 408 fallback (5-line bloc tweak)

When wait-online returns 408, BEFORE emitting `failed`, do:

```dart
final fallback = await alarmRepository.fetchAlarmByIdentityName(slug);
if (fallback != null) {
  emit(AlarmProvisionV16State.success(device: fallback));
  return;
}
emit(failed);
```

Catches the late-register race we hit all evening. Backend creates the row 30-45s after the iPhone's wait-online deadline; without this fallback the user sees "fail" but the alarm shows up in the device list anyway. Backend already has a final-grace check (deployed tonight); this is the iPhone-side belt-and-suspenders.

### 2. Aggressive LA token re-registration

`auth_bloc::_listenForLiveActivityToken` should re-upload tokens to Cloud on EVERY app foreground, not just on initial launch.

Tonight we observed the cloud-side token store empty out after some app lifecycle event and never get refilled — Lambda broker returned `la=n halo=n fcm=0 apns_raw=0` even though the iPhone had Notifications enabled. Without aggressive re-registration, Live Activity cards silently die in the field. This is the sustainability issue.

### 3. Log export share-sheet UI

Long-press the version label in Settings → opens iOS share sheet with the on-disk debug log file.

Already 90% built in `debug_log_collector.dart::diskFilePath()` — just needs the UI gesture wired in. Without this, in-field reproductions are blind.

### 4. Onboarding: post-factory-reset settle delay

Tonight's evidence: scanning QR on a Halo immediately after factory reset can fail because the softAP is still "warming up." iPhone's `connectToHaloAp` returns false silently.

Either:
- Add a fixed 3-5s delay between QR scan and `connectToHaloAp`, OR
- Scan iOS WiFi list a couple of times to verify the SSID is actually visible before calling join

### 5. iOS sticky-deny cache breaks repeat onboards (HIGH PRIORITY)

**CORRECTION (2026-05-03)**: An earlier draft of this note suggested using `WiFiForIoTPlugin.removeWifiNetwork(ssid)` to programmatically clear iOS's deny cache. That was wrong. The Flutter dev verified by reading the plugin source at `~/.pub-cache/hosted/pub.dev/wifi_iot-0.3.19+2/ios/Classes/SwiftWifiIotPlugin.swift:317` — it calls `NEHotspotConfigurationManager.removeConfiguration(forSSID:)`, which per Apple's docs **only removes hotspot configurations the calling app installed**. It does NOT touch iOS's system-level sticky-deny cache. That cache is only writable via Settings → WiFi → Forget This Network, which has system privilege apps don't have.

The correct approach is two parts:

**4a — Hygiene: prefix `removeWifiNetwork('jupyter-alarm-')` before every join attempt.**
Cleans up any stale app-installed configs from prior attempts. Won't fix sticky-deny but prevents accumulation of dead config records. Free defensive programming.

**4b — Honest UX after 2 consecutive failures.**
After 2 failed `connect` attempts, assume iOS has sticky-denied this SSID and surface explicit guidance:

```
We can't reach your Halo's WiFi.

iOS may have remembered a previous failed attempt.

To fix:
  1. Open iOS Settings → WiFi
  2. Tap the (i) next to 'jupyter-alarm-eaa324'
  3. Tap "Forget This Network"
  4. Come back and tap Retry

[ Open Settings ]   [ Retry ]
```

The Open Settings button uses the `app-prefs:` URL scheme to deep-link. The user does the privileged action; the app makes it as frictionless as possible.

Pretending the app can auto-resolve the deny cache would create a Heisenbug (works sometimes, silently fails others). Honest UX is more reliable than wishful API wrapping.

**Pre-flight scan**: NOT viable on iOS. iOS sandboxes the WiFi scan list from apps for privacy. The only options for "is the SSID broadcasting?" are:
- **Variant A**: ping `192.168.4.1` over current WiFi — only works if iPhone is ALREADY on the Halo softAP (manual join from Settings). Useful as a "user is already on AP, skip our connect" fast-path. NOT a pre-flight visibility check.
- **Variant B**: UI gate asking the user to confirm the SSID is visible in their iOS Settings → WiFi list before proceeding. Honest, simple, ~30 lines.

Variant B is the recommended pre-flight gate.

### 6. Type=OUTDOOR from QR ends up as INDOOR in the row (cosmetic) — **backend done**

Bloc reads `type` from QR field 1 and stores in `_haloType`, but `saveMetadata`'s PATCH body doesn't include it. Two-pronged fix shipped tonight on backend:

- **Item 7 (PATCH writable)**: `UpdateAlarmDeviceSerializer.Meta.extra_kwargs["type"]: read_only=True` was removed. PATCH `/api/alarm-devices/{id}/` with `{"type": "OUTDOOR"}` now returns 200 and persists. Validated on Mill-Valley: PATCH OUTDOOR (200), PATCH BOGUS (400 with "is not a valid choice"), PATCH INDOOR (200). `AlarmType` TextChoices is the validation gate.
- **Item 7.5 (firmware-baked promotion)**: `enrich_and_publish_ha_discovery` now parses `serial_number` from `/api/status` (`JUP-OUTDR-XXXXXX` → OUTDOOR, `JUP-INDR-XXXXXX` → INDOOR) and promotes the row's `type` from default INDOOR. **Validated end-to-end on Mill-Valley** (id=18, real Halo at 192.168.1.222 with serial JUP-OUTDR-EAA324): task ran in 109ms, log shows `halo_enriched id=18 fields=['name', 'type']`, DB went `INDOOR → OUTDOOR`, HA discovery republished as `model=Halo-OUTDOOR`. Promotion only fires when row is at default INDOOR — a user PATCH (Item 7) wins later.

Net: even if the bloc never sends `type`, the firmware serial will fix it asynchronously after onboard. If the bloc DOES send `type` in `saveMetadata`, that wins immediately. Both paths converge on the right answer.

### 6b. Wait-online response now carries onboarding timing — **backend done (Item 6)**

`AlarmWaitOnlineView` now stamps a `started_at` Redis key when the onboard-payload is fetched (1800s TTL, survives webhook clear-pending). On 200 success the response includes:

```json
{
  "device": {...},
  "time_to_register_seconds": 23.4,
  "wait_seconds_elapsed": 18.1
}
```

On 408 timeout:

```json
{
  "detail": "Halo did not register in time",
  "time_to_register_seconds": null,
  "last_register_check_at": "2026-05-03T...",
  "onboard_started_at_known": true
}
```

Plus a final-grace DB check after the deadline catches sub-second-late registers. Use `time_to_register_seconds` for any UX correlation (e.g. the user-facing "took 23s" toast, or telemetry).

### 7. Celery queue routing — **backend done**

Both `enrich_and_publish_ha_discovery` and `publish_ha_discovery_if_needed` now have `queue="hub_operations_queue"` in their `@shared_task` decorators. Before this fix, they landed on `default` queue which no worker consumes — explaining why type promotion + HA discovery silently never fired despite the webhook calling `.delay()`. Validated: `worker_automation@/root` now lists both tasks as registered consumers of `hub_operations_queue`.

## Backend state at end of tonight

- v1.6 webhook: stable (the version that's been creating rows id=6, id=8, id=9, id=11, id=12, id=15 successfully all evening)
- `factory_reset_with_verify`: deployed in `alarm/services/halo_recovery.py` — cancel-first + verify-confirmed + 2-attempt retry
- `RetrieveDeleteAlarmDeviceView.destroy()`: rewritten to use auto-confirm flow (no LA dependency)
- Wait-online final-grace check: deployed
- **Item 6** `time_to_register_seconds` in wait-online responses: deployed (T1/T2/T3 green, T4 will validate naturally on next real onboard)
- **Item 7** `type` writable on `UpdateAlarmDeviceSerializer`: deployed and validated (PATCH OUTDOOR/BOGUS/INDOOR all behaved correctly)
- **Item 7.5** Halo serial parsing + INDOOR→OUTDOOR promotion in Celery enrichment: deployed and validated end-to-end on real hardware (id=18 went INDOOR → OUTDOOR after task fired)
- **Celery routing fix**: `enrich_and_publish_ha_discovery` + `publish_ha_discovery_if_needed` now correctly route to `hub_operations_queue` (was silently going to unconsumed `default` queue)
- All committed to `feat/halo-onboard-v1.6` on `jupytertemi/jupyter-hub-controller`

## Combined system reliability

- Firmware: OTA'd by Long tonight with 4 fixes (nonce parsing, cancel-always-replies, stale-state clearing, diagnostic logging on mismatch). Bench-cycled successfully end-to-end.
- Backend: handles the previous firmware bugs gracefully + retries.
- iPhone (Build 155): `isHidden: true` revert closes the cold-scan reliability gap.

Once Build 155 lands on phones, the onboard + offboard cycle should be ~100% reliable in normal conditions.
