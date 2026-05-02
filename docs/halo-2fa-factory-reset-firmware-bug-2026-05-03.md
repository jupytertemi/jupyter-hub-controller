# Halo 2FA Factory Reset — Firmware Reliability Bug Report

**Date**: 2026-05-03 (~02:00 AEST)
**Firmware**: v2.21.0 (post-`/wifisave` reboot fix flashed earlier today)
**Hardware**: bench Halo `eaa324`, JUP-OUTDR-EAA324, on Mill-Valley hub at `192.168.1.222`
**Hub**: Mill-Valley, deployed v1.6 backend with auto-confirm offboard

## Summary

The MQTT 2FA factory_reset chain is **intermittently** ignored by the firmware after the first successful run within a session. After the first offboard works cleanly, subsequent offboard attempts on the same Halo (without a power-cycle in between) fail in different ways at the firmware end. The hub publishes correctly each time; the broker accepts each PUBLISH; but the Halo's confirm handler either silently drops the wipe, or the firmware's pending-state machine returns contradictory denials.

This blocks production-grade reliability of remote offboard.

## Reproduction (4 offboards in one session, 1st worked, 3 subsequent failed differently)

### Offboard #1 — clean success, all expected behavior
- 01:59:37.256 hub → `factory_reset` + secret
- 01:59:37.356 Halo → `pending`, nonce=1409247988, expires_in=60, serial=JUP-OUTDR-EAA324
- 01:59:39.340 hub → `confirm_factory_reset` + nonce=1409247988
- **Buzzer 3-beep pattern played (heard by user)**
- Halo dropped off home WiFi (transfer_server stopped seeing register heartbeats, 192.168.1.222 ping 100% loss)
- AP `jupyter-alarm-eaa324` came up
- Customer-facing flow worked end-to-end

### Offboard #2 — published the same way, zero firmware effect
- 02:02:56.925 hub → `factory_reset` + (new fresh) secret
- 02:02:57.026 Halo → `pending`, nonce=2256262446
- 02:02:58.945 hub → `confirm_factory_reset` + nonce=2256262446
- **No buzzer**
- transfer_server kept seeing register heartbeats at 02:03:00, 02:03:31 (same TCP socket, port 56849)
- `curl http://192.168.1.222/api/status` returned `uptime: 164s, reset_reason: software` — the last reset was the original onboard reboot at 02:01, NOT this offboard. Halo did not wipe, did not reboot.

The hub's auto-confirm code uses the same publish path as Offboard #1 (which worked) and as the bench test earlier today (which also worked twice). The MQTT broker logs show CONNACK + PUBLISH on both attempts. The Halo IS subscribed to `/jupyter-alarm-eaa324/recovery` (it answered with `pending` for the prior `factory_reset` PUBLISH on the same MQTT session).

So: Halo received the confirm, but didn't act on it. Possible firmware causes:
1. The firmware's `factory_reset_pending` flag was cleared between the `pending` publish and the `confirm` arrival (~1.9s gap)
2. MQTT session dropped briefly between the two messages and reconnected; with `clean_session: true` the queued QoS 1 confirm was lost
3. The confirm handler ran but the `mode_factory_reset` script never fired (would explain no buzzer)
4. The wipe started, errored mid-NVS, and the device kept running on the half-wiped NVS

### Offboard #3 (manual recovery via mosquitto_pub) — explicit firmware self-contradiction
- Hub → `factory_reset` + secret
- Halo → `pending`, nonce=2221621580
- Hub → `confirm_factory_reset` + nonce=2221621580 (the exact same nonce)
- **Halo → `denied`, reason=`invalid_nonce`**

The Halo issued a nonce, then immediately rejected the same nonce on confirm. This is a self-contradiction unless the firmware compares confirm_factory_reset's nonce against a stored value that's stale from a prior pending state (Offboard #2's nonce 2256262446 perhaps?). Best theory: the `factory_reset` handler didn't overwrite `factory_reset_nonce` with the new random value when an existing pending state was active, so the firmware's internal nonce diverged from the nonce broadcasted in the `pending` reply.

### Offboard #4 (recovery attempt with cancel_factory_reset first) — firmware MQTT silent
- Hub → `cancel_factory_reset` (no payload data) → **no reply on `/recovery/status`**
- Hub → `factory_reset` + secret → **no reply on `/recovery/status` (no `pending`, no `denied`)**

After repeated stuck-state attempts, the firmware's MQTT 2FA subsystem appears to stop responding entirely, while:
- TCP register on port 4444 continues normally (transfer_server heartbeats every 30s)
- HTTP `/api/status` continues to respond (we curl'd it during the failure)
- `streamer_connected: false`, `streamer_paused: true` (audio streamer in degraded state, separate from MQTT)

This suggests the MQTT recovery subscription either disconnected or the firmware's recovery state machine is hung in a state where it doesn't process incoming commands.

## What the hub-side code does

`alarm/services/halo_recovery.py::initiate_factory_reset` — connects to local EMQX, subscribes to `/{slug}/recovery/status`, publishes `factory_reset` with the device_secret. Returns the `pending` payload on success.

`alarm/services/halo_recovery.py::confirm_factory_reset` — connects to EMQX, publishes `confirm_factory_reset` with the nonce captured from the prior pending response. Fire-and-forget (does NOT subscribe to `/status` to verify the firmware acted on it — this is a hub-side gap we'll close, see below).

`alarm/views.py::RetrieveDeleteAlarmDeviceView.destroy` — auto-confirm flow, calls initiate then confirm back-to-back, then runs hub-side cleanup (DB row delete, HA scripts/automations).

## Hub-side mitigations we're shipping

1. **Cancel-before-reset**: every offboard issues `{"command":"cancel_factory_reset"}` before `factory_reset` to clear any stale pending state. Brief pause (~500ms) between them. Should immunise the chain from a previous session leaving the firmware mid-pending.

2. **Confirm-and-verify**: after publishing `confirm_factory_reset`, hub stays subscribed to `/recovery/status` for 5s and looks for `{"factory_reset":"confirmed","status":"resetting"}`. If the Halo's confirm handler silently drops (Offboard #2 case), the hub logs the failure and surfaces it in the API response so the app can prompt the user to power-cycle.

3. **No retry on `invalid_nonce`** (per your brief): logged as ERROR so we can correlate against firmware crashes / state corruption.

## What we'd like you to investigate firmware-side

1. **Race between `pending` publish and `confirm_factory_reset` arrival** — does the firmware update `factory_reset_pending` and `factory_reset_nonce` atomically before publishing `pending`? Is there a window where confirm-handler reads a stale nonce?

2. **Idempotency of repeated `factory_reset`** — when a second `factory_reset` arrives while the first is pending, does the firmware:
   - (a) Replace the pending state cleanly with a new nonce (correct), or
   - (b) Keep the old `factory_reset_nonce` while broadcasting a new one in `pending` (the bug we observed in Offboard #3)?

3. **MQTT subscription resilience** — what triggers the recovery subscription to stop responding (Offboard #4 case)? Is there a state where the Halo silently stops processing recovery commands? Possibly tied to the `streamer` going into paused state?

4. **Wipe-failure modes** — if `nvs_flash_erase()` errors partway, what does the firmware do? Does the current behaviour leave the device half-wiped but still running (Offboard #2 case)? Should there be a watchdog that reboots if a confirmed factory_reset doesn't complete the wipe within N seconds?

5. **`cancel_factory_reset` reliability** — should the firmware ALWAYS reply `{"factory_reset":"cancelled"}` even if there's no active pending state, so the hub can use it as a session-reset primitive? Currently it appears to silently no-op when there's no pending.

## Test scenarios that pass / fail today

| Scenario | Result |
|---|---|
| Bench test: mosquitto_pub direct, fresh boot, single attempt | ✅ wipe + reboot + AP, 3-beep buzzer |
| Offboard #1 (real DELETE flow, fresh boot) | ✅ same as bench |
| Offboard #2 (DELETE flow, ~30s after fresh onboard, no power-cycle between) | ❌ silent drop, Halo stays running |
| Offboard #3 (after #2 silent drop, retry via mosquitto_pub) | ❌ self-contradicting `invalid_nonce` |
| Offboard #4 (after #3, retry with cancel + factory_reset) | ❌ firmware MQTT 2FA fully silent |
| Power-cycle Halo, then retry | (untested as of this note — predicted ✅) |

## Recovery procedure tonight

Tonight's bench Halo is in stuck state. Recovery options:
1. **Power-cycle via mains** — predicted to clear firmware MQTT hang. Halo NVS still has bonded creds → rejoin home WiFi → resume registering → fresh `factory_reset` flow should work. If not, escalate to:
2. **Manual NVS wipe via serial console** — `esptool.py erase_region 0x9000 0x6000` or equivalent + reset.

## Action items

- [ ] Firmware dev: investigate items 1-5 above
- [x] Hub backend: deploy cancel-first + confirm-verify (this session)
- [ ] Hub backend: add `published_to_topic` audit log per recovery message for cross-debugging
- [ ] Firmware dev: serial console capture during a Stuck-State reproduction to see exactly which handler runs vs is silent

---

Bench Halo eaa324 still on `192.168.1.222`. device_secret is in AlarmDevice id=14 (latest recovery attempt's row, may be auto-deleted by morning depending on TTL on the pending registry entry).
