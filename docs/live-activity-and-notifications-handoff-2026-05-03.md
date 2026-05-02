# Live Activity + Notifications — Handoff Brief

**Date**: 2026-05-03
**For**: junior Flutter dev (review pass)
**Status reported by user**: LA on lock screen + regular push notifications both not landing on iPhone. In-app Halo Live Activity card works (that's the on-device widget rendering, not a push).

This brief documents the hub-side surface area only. The full system spans Flutter app → cloud Lambda broker → hub `.env` → publisher → APNs/FCM. The hub side is one slice; the failures could be in any layer.

---

## Architecture (hub-direct path)

```
iPhone (Flutter app)
  ├─ requests LA push-to-start token, registers APNs raw token, FCM token
  ↓ POST to cloud Lambda broker
Cloud Lambda broker
  ├─ stores (hub_slug, owner) → (LA token, APNs raw token, FCM token)
  ├─ on hub poll (every 10 min via systemd timer) → returns latest tokens
  ↓ HTTP GET (APNS_TOKEN_BROKER_URL)
Hub /root/jupyter-hub-controller/.env
  ├─ writes LIVE_ACTIVITY_START_TOKEN, HALO_CHARGING_START_TOKEN,
  │   FCM_REGISTRATION_IDS, APNS_DEVICE_TOKENS, LIVE_ACTIVITY_TOKEN_REFRESHED
  ↓ EnvironmentFile= picked up at restart
Hub systemd service: live-activity-publisher.service
  ├─ subscribes to local EMQX
  │   /events                       (AI events → LA + alert + FCM)
  │   +/status                      (Halo telemetry → LA + alert + FCM)
  │   /halo_offboard_2fa_pending    (offboard 2FA → LA only)
  ├─ on event → APNs LA push, APNs alert push, FCM push
  ↓ HTTPS to api.push.apple.com / fcm.googleapis.com
Apple APNs / Firebase
  ↓
iPhone receives
```

The cloud-broker → hub-poll mechanism replaced an earlier cloud→SQS→Django path that was broken (per `~/.claude/projects/-Users-topsycombs/memory/hub-direct-push-to-start.md`). Hub-direct is the working architecture; the question is which piece in it isn't doing its job today.

---

## What lives where

### Hub side (this repo + this hub)

| Path | Role |
|---|---|
| `/root/jupyter-hub-controller/live_activity_publisher.py` | The publisher. Subscribes EMQX → fans out to APNs LA, APNs alert, FCM. **NOT version-controlled in this repo today** — only on hubs. Worth pulling into git. |
| `/etc/systemd/system/live-activity-publisher.service` | systemd unit that runs the publisher. `EnvironmentFile=/root/jupyter-hub-controller/.env`, `Restart=on-failure`. |
| systemd timer (likely `live-activity-token-refresh.timer` — confirm on hub) | Calls the cloud broker every 10 min, writes refreshed tokens into `.env`, restarts the publisher service so it picks up the new `.env`. |
| `/root/jupyter-hub-controller/.env` | Holds: `APNS_BUNDLE_ID`, `APNS_TEAM_ID`, `APNS_KEY_ID`, `APNS_PRIVATE_KEY_PATH`, `APNS_TOKEN_BROKER_URL`, `FIREBASE_CRED_PATH`, `FCM_REGISTRATION_IDS`, `APNS_DEVICE_TOKENS`, `LIVE_ACTIVITY_START_TOKEN`, `HALO_CHARGING_START_TOKEN`, `LIVE_ACTIVITY_TOKEN_REFRESHED` (unix timestamp). |
| `/var/log/la-publisher-metrics.jsonl` | One JSON record per push attempt: `{ts, hub_slug, push_type, tag, status, http_status, error}`. Parse with `jq`. |
| `alarm/services/halo_recovery.py::publish_offboard_2fa_pending(...)` | Hub-side trigger that publishes the MQTT message the publisher subscribes to for the Halo offboard 2FA Live Activity. Wired but unused under the auto-confirm offboard path. Kept for the LA-driven offboard variant. |

### Cloud side (NOT in this repo — separate Lambda function)

The Lambda broker is the registration + retrieval layer. Find it via the URL in `.env`:
```
APNS_TOKEN_BROKER_URL=https://g6p27sc853...lambda-url.../...
```
That Lambda owns:
- The token store (DynamoDB? secrets? S3? — junior dev should confirm)
- The Flutter-side endpoint where the iPhone POSTs new tokens
- The hub-side endpoint where the hub GETs current tokens

If tokens are missing, the question is "did the iPhone register them" vs "is the Lambda returning empty arrays." Both produce `la=EMPTY` on the hub.

### Flutter side (NOT this repo — `jupyter-app-rebuild`)

- `auth_bloc::_listenForLiveActivityToken` is the LA token registration path (per `flutter-dev-note-build-155-156-2026-05-03.md` Build 156 item 2).
- APNs raw token + FCM token registration happens during `pushNotificationsService` initialization. Junior dev knows which file.
- Earlier observation in this repo's session-history: the cloud-side token store empties out after some app-lifecycle event and never gets refilled — that's the working hypothesis but not proven.

---

## What I observed on Mill-Valley right now

Service status: **active**, restarting cleanly every 10 min (consistent with the token-refresh timer pattern).

Latest startup log line (every 10 min):
```
starting publisher v4  bundle=com.app.jupyter.dev la=EMPTY halo=EMPTY fcm=1 apns_raw=1 firebase=yes
```

Translation:
- `la=EMPTY` → `LIVE_ACTIVITY_START_TOKEN` is empty in `.env` **as of the last refresh**. Pushes to AI-event LA will skip with "no token."
- `halo=EMPTY` → `HALO_CHARGING_START_TOKEN` is empty. Halo charging LA pushes will skip too.
- `fcm=1` → 1 FCM token loaded.
- `apns_raw=1` → 1 APNs raw token loaded.
- `firebase=yes` → Firebase Admin SDK initialized.

Note: `LIVE_ACTIVITY_TOKEN_REFRESHED=1777764019` (2026-05-02 ~22:00 UTC) means the refresh timer DID run. The Lambda broker returned empty for the LA tokens at that point. Earlier in the same day, LA tokens were populated — see metrics below.

---

## Empirical diagnosis from `/var/log/la-publisher-metrics.jsonl`

Aggregating ~28 push attempts across the last few days on Mill-Valley:

| push_type | status | http_status | count |
|---|---|---|---|
| `liveactivity` | ok | 200 | 14 |
| `liveactivity` | skipped | (no token) | 1 |
| `alert` (APNs raw) | ok | 200 | 13 |
| `alert` | http_fail | 400 BadDeviceToken | 1 (different hub: seattle, with placeholder token `...abcdef` — not user-facing) |
| `fcm` | http_fail | 0 (failed=1) | 14 |

Sample successful entries (real recent traffic, all returning HTTP 200 from Apple):
```
1777700580 liveactivity ok 200  LA/parcel_theft_detected event=716fb64a-...
1777700581 alert        ok 200  alert/parcel_theft_detected dev=...cbc353
1777700604 liveactivity ok 200  LA/loitering_detected event=1777700577.811086-p2ev8o
1777730037 liveactivity ok 200  LA/garage_detected event=la-test-1777730036
1777730038 alert        ok 200  alert/garage_detected dev=...cbc353
```

Latest entry (this morning's offboard cycle):
```
1777736825 liveactivity skipped (no token)  LA/halo_offboard_2fa slug=jupyter-alarm-eaa324 nonce=3726772175
```

### What this tells us about the pipeline (**verified, not guesses**)

Walking the pipeline upstream → downstream:

- ✅ **AI dockers → MQTT `/events`**: working. Real event UUIDs flowing through (`716fb64a-...`, `8496d85a-...`, `1777700577.811086-p2ev8o`).
- ✅ **MQTT subscriptions in publisher**: working. Publisher logs `MQTT connected rc=Success` and subscribes to `/events`, `+/status`, `/halo_offboard_2fa_pending`.
- ✅ **Classifier**: working. PARCEL → `parcel_theft_detected`, PERSON+loitering → `loitering_detected`, CAR → `garage_detected`, all dispatching correctly.
- ✅ **Hub → Apple APNs**: working. 27 of 28 pushes returned **HTTP 200 from `api.push.apple.com`**. Apple accepted the JWT, the topic, the payload, and the device token format.
- ❌ **Apple → iPhone delivery**: **broken silently.** APNs returns 200 even when the token is registered to the wrong environment, the app was uninstalled, or the token is otherwise dead. Apple does not tell the publisher inline; you only learn via the APNs feedback channel (which the publisher does not poll).
- ❌ **Hub → FCM → Android/iOS fallback**: **broken loudly.** Every single FCM push fails (`failed=1`). The publisher's metric only records `failed=1` without the underlying Firebase reason — `live_activity_publisher.py:357-358` aggregates the per-token response counts but doesn't log the per-token error message.

### The one thing that DOES correlate with "user gets nothing"

**Diagnostic timeline of this section** — I include both turns because the reasoning matters:

**Turn 1 (initial draft):** led with "APNs key / environment mismatch" as the prime suspect.

**Turn 2 (retraction):** Temi reported "the Halo Live Activity card works." I retracted the .p8 hypothesis on the assumption that this was the **system** variant (lock-screen, app-killed) — which would have proven the .p8 + endpoint + at least one token were healthy.

**Turn 3 (un-retraction, current):** Temi ran the disambiguation test — killed Jupyter from the app switcher, waited 30s, plugged in the Halo. **No system Live Activity appeared.** That means the Halo LA he had been seeing is the **in-app** variant (`Activity.request(...)` from inside the running Flutter app), which bypasses APNs entirely and proves nothing about push delivery. The .p8 / environment hypothesis is back on the lead spot.

The corrected reading of the evidence: hub-side fires 27 of 28 pushes successfully (HTTP 200 from `api.push.apple.com`), and **zero** of those reach Temi's iPhone — across LA push-to-start, plain APNs alert, and FCM. That symmetric across-the-board failure with 200 OK from Apple is the textbook signature of an APNs environment / scope mismatch, not per-token freshness.

### Hypotheses, in current likelihood order

1. **.p8 key scope mismatch (highest).** The auth key at `APNS_PRIVATE_KEY_PATH` (kid `2S6GK89DYS`) may only be registered for Development in Apple Developer portal. Publisher pushes to `https://api.push.apple.com` (production endpoint). Apple validates the JWT structure and key ID, returns 200, then silently drops because the key isn't authorized for that environment.

   *Verification:* Apple Developer portal → Keys → Auth Key for Apple Push Notifications service → confirm Production is checked.

2. **`aps-environment` entitlement mismatch (high).** If the Flutter iOS build's entitlement is `aps-environment: development` (as a Xcode debug build would be), the iPhone registers with **sandbox APNs**. Hub pushes to production endpoint. Production has no registration for that token, accepts the request format, returns 200, drops silently.

   *Verification:* in Xcode, open the Flutter project, Signing & Capabilities → Push Notifications → verify the entitlement value. For TestFlight builds it should be `production`. For Xcode dev installs it'll be `development`.

   Concrete cross-check the publisher could do: try pushing to `https://api.sandbox.push.apple.com/3/device/<token>` instead of production. If sandbox returns 200 AND the iPhone actually receives, that's the proof — the iPhone is registered to sandbox.

3. **All tokens stale (medium).** App was reinstalled or rotated, the cloud Lambda kept the old tokens, Apple has unregistered them but hasn't yet emitted `410 BadDeviceToken`. Less likely than #1 or #2 because Apple typically emits `410` reasonably fast for permanently-dead tokens. We see 13 alert pushes returning 200 in a row over multiple days — Apple would normally have 410'd by now.

4. **Per-token refresh asymmetry (low, not retracted but downgraded).** It's still worth checking whether the cloud Lambda refreshes all three token types symmetrically, but it's not the lead suspect anymore. The latest log line shows `la=EMPTY halo=EMPTY fcm=1 apns_raw=1`, which means the LA-token refresh path can return empty while alert/FCM tokens remain populated — so the asymmetry is real, but it doesn't explain why the populated alert + FCM tokens also yield zero delivery.

### For FCM specifically

`failed=1` on every push, no per-token reason logged. Need the `live_activity_publisher.py:357-358` patch to surface the Firebase error. Most likely causes once we have the error:
- Firebase service-account credentials at `FIREBASE_CRED_PATH` rotated/revoked
- Firebase project missing the APNs auth key (iOS-via-FCM fails; Android still works)
- FCM token stale from app reinstall

If the .p8 / aps-environment thing is the real APNs root cause, FCM-iOS would *also* be broken because Firebase delivers iOS pushes via the same APNs key uploaded into the Firebase project. So #1 and #2 above could be a single root cause for all three failures.

---

## Failure-mode breakdown (best-guess but **NOT verified** — junior dev should confirm each)

| Symptom user reports | What hub log says | First place to look |
|---|---|---|
| Lock-screen Live Activity card never appears | `la=EMPTY` at every restart | iPhone → Lambda broker. Is the LA push-to-start token being POSTed when Flutter requests it? See iPhone-side `_listenForLiveActivityToken`. Then the Lambda's PUT-token endpoint. Is it persisting? |
| Halo charging Live Activity card never appears | `halo=EMPTY` at every restart | Same iPhone-side path but for the `HaloChargingActivityAttributes` token. |
| Regular push notifications not landing | `fcm=1`, `apns_raw=1` at every restart — should be working | (a) Run a manual MQTT publish to `/events` on the hub to force a push; check if `/var/log/la-publisher-metrics.jsonl` records `ok` or `http_fail`. (b) If hub side says `ok`, the failure is between Apple/Firebase and the iPhone (token might be wrong, expired, app entitlements). (c) If hub side says `http_fail` with status 410 (BadDeviceToken), the tokens are stale. |
| In-app Halo Live Activity card works | Not a push at all — that's an `Activity.request(...)` call from inside the running Flutter app | Different code path entirely. Doesn't tell us anything about the push-to-start side. |

---

## Quick smoke test the junior dev can run

On the hub:
```bash
# Trigger an AI event push manually:
docker exec emqx mosquitto_pub -h 127.0.0.1 -t /events -m '{
  "label": "PARCEL",
  "parcel_status": "parcel_theft_attempt",
  "camera_name": "test_cam",
  "event_id": "smoke-$(date +%s)"
}'

# Watch the publisher react:
journalctl -u live-activity-publisher.service -f

# Watch the metrics file:
tail -F /var/log/la-publisher-metrics.jsonl | jq .
```

Expected hub-side trace if the alert path is healthy:
```
push_type=alert    status=ok  http_status=200    tag="alert/parcel_theft_detected dev=...XXXXXX"
push_type=fcm      status=ok  http_status=1      tag="Parcel pickup by unknown person"
push_type=liveactivity status=skipped error="no token"
```

If the iPhone gets nothing despite hub-side `ok` rows, the failure is downstream of the hub.
If the hub-side row shows `http_fail` with a 4xx, the tokens are bad — Lambda broker + Flutter registration is the trail to follow.

---

## What I did NOT change

I did NOT touch `live_activity_publisher.py`, the systemd unit, the .env tokens, or any LA-related Flutter code. The Halo backend session this morning only changed:
- alarm/serializers.py, services/halo_enrichment.py, services/pending_onboard.py, tasks.py, urls.py, views.py, views_halo_onboard.py
- new files: alarm/services/halo_recovery.py, alarm/views_halo_recovery.py
- new docs: flutter-dev-note-build-155-156, halo-2fa-factory-reset-firmware-bug

None of that is on the LA delivery path. The two relevant cross-overs:
1. `alarm/services/halo_recovery.py::publish_offboard_2fa_pending` is the Halo-offboard-2FA LA trigger. Currently unused because the auto-confirm offboard path doesn't need it. Kept for the LA-driven variant.
2. `RetrieveDeleteAlarmDeviceView.destroy()` no longer relies on Live Activity to confirm offboards — it uses the firmware's own `factory_reset_with_verify` chain instead. So even if LA is broken, offboard works.

---

## Action items for junior dev (priority-ordered)

Disambiguation test result confirmed: **system-level Halo LA does not appear** when Jupyter is force-killed and the Halo is plugged in. The Halo LA Temi has been seeing is the in-app `Activity.request(...)` variant only. So zero system pushes are landing on the iPhone, despite Apple returning 200 OK for 27 of 28 publisher attempts. Search shifts to environment / scope mismatch as the lead.

1. **Apple Developer portal — verify the .p8 key scope.** Open the Auth Key with kid `2S6GK89DYS`. Confirm "Apple Push Notifications service (APNs)" is enabled with **both Production and Development** boxes — or whichever the project intends. If only Development is enabled and the publisher pushes to `api.push.apple.com` (production), Apple validates the JWT and returns 200 silently dropping every push. This is the highest-priority item.

2. **Verify `aps-environment` entitlement in the Flutter iOS build.** Open the rebuild project in Xcode → Runner target → Signing & Capabilities → Push Notifications. Check the entitlement file (`Runner.entitlements`). For TestFlight builds the value should be `production`. If it's `development`, the iPhone is registering with sandbox APNs and the production endpoint will silently drop everything. The fact that Temi installs Build 154/155 via TestFlight means `production` is expected — but worth verifying it matches what's actually shipping.

3. **Sandbox endpoint cross-check** (definitive test). On the hub, temporarily try one push to `https://api.sandbox.push.apple.com/3/device/<APNS_DEVICE_TOKEN>` with the same JWT and payload. If sandbox returns 200 AND the iPhone receives it, the iPhone is registered to sandbox and the production endpoint is wrong for this build. If sandbox also silently 200s with no delivery, the issue is elsewhere. ~10 lines of curl/python — junior dev can run from the hub directly.

4. **Patch `live_activity_publisher.py` to log per-token Firebase failures.** Currently `live_activity_publisher.py:357-358` only logs counts. Adding `for r in resp.responses: if not r.success: log.error("FCM token-fail: %s", r.exception)` after `messaging.send_each_for_multicast` will surface the Firebase reason. If the .p8 / env theory turns out to be the root, FCM-iOS will likely fail the same way (Firebase delivers iOS via the same APNs key uploaded to the Firebase project) and the surfaced error will say `Auth error from APNs` or similar.

5. **Pull `live_activity_publisher.py` into git.** It's on hubs but not version-controlled — drift risk on every gold-image refresh.

6. **Token freshness audit (lower priority now, but still useful).** On the iPhone, force-quit + relaunch Jupyter. Watch the cloud Lambda store update. Watch the hub's next 10-min `.env` refresh. Confirm all three token types populate symmetrically. The `la=EMPTY halo=EMPTY` we observed at one point hints at an asymmetric refresh path — separate issue from #1-3 but worth fixing.

7. **Add a per-token presence health check on the hub.** Warn loudly when any of `LIVE_ACTIVITY_START_TOKEN`, `HALO_CHARGING_START_TOKEN`, `APNS_DEVICE_TOKENS` is empty for more than N consecutive refreshes. Would have caught the silent token-go-empty case before user-visible testing.

---

## Memory pointers

- `~/.claude/projects/-Users-topsycombs/memory/hub-direct-push-to-start.md` — architecture overview
- `~/.claude/projects/-Users-topsycombs/memory/live-activity-debugging.md` — prior debugging notes (stale tokens, duplicate device entries)
- `~/.claude/projects/-Users-topsycombs/memory/user-phone-device.md` — Temi's iPhone is device id 58, iOS 18.7.3, the canonical test target
