# AI Constants Healer — what it is, why it exists, what to do during offboard/onboard

> **Audience:** any backend developer touching the hub or its deploy/onboard
> flows, with **no prior context**. Read top-to-bottom — no terms used before
> they're defined.

---

## 1. The 30-second summary

A small periodic task on the hub auto-corrects two configuration values
inside each AI Docker container's `constants.py` file whenever they get
clobbered by a container image rebuild. **No operator action is required
during offboard or onboard.** This document is just so you understand
what's running and why, in case you see it in logs or need to debug it.

---

## 2. Background you need to understand the problem

### 2.1 What runs on a hub

A jupyter hub is a small Linux box (Radxa Rock 5B or similar) running
several Docker containers, including these "AI" containers — each one is
a Python service that processes camera frames:

| Container name (Docker) | Purpose |
|---|---|
| `number_plate_detection` | Vehicle / license plate (a.k.a. VehicleAI) |
| `face_recognition` | Faces (a.k.a. FaceAI) |
| `parcel_detection` | Parcels (a.k.a. ParcelAI) |
| `sound_detection` | Audio events (a.k.a. UnusualSoundsAI) |

Each AI container has a Python file at `/usr/src/app/constants.py` that
holds all its configuration — model paths, thresholds, MQTT topic names,
etc. About 200 lines of Python.

### 2.2 The bind-mount

To allow the hub to **tune** an AI's settings without rebuilding the
Docker image, the hub's `docker-compose.yml` bind-mounts a host file
**over** the container's in-image `constants.py`:

```yaml
# docker-compose.yml
number_plate_detection:
  volumes:
    - ./pilot_vehicle_ai/constants.py:/usr/src/app/constants.py:ro
```

So at runtime the container sees the host's file, not the file baked into
the image. The host file lives at one of these paths (one per AI):

| AI container | Host file path |
|---|---|
| `number_plate_detection` | `/root/jupyter-container/pilot_vehicle_ai/constants.py` |
| `face_recognition` | `/root/jupyter-container/pilot_face_recognition_ai/constants.py` |
| `parcel_detection` | `/root/jupyter-container/pilot_parcel_theft_AI/constants.py` |
| `sound_detection` | `/root/jupyter-container/pilot_unusual_sounds_ai/constants.py` |

These host files have the Linux **immutable bit** set
(`chattr +i`) so they can't be overwritten by a normal `cp`. Only code
that explicitly does `chattr -i`, edits, then `chattr +i` again can change
them.

### 2.3 The two kinds of values inside `constants.py`

**Two completely different categories of values share the same file:**

**(a) Developer code-level constants** — written by engineers, version-controlled in the AI's GitHub repo. Examples for VehicleAI:
```python
ALPR_CONFIDENCE = 0.45                       # ML threshold
PARKING_THRESHOLD = 3.0                      # state-machine timing
GARAGE_AUTO_CLOSE_DELAY_S = 300              # behavior config
DEPARTED_REQUIRES_PRIOR = ["Parking", ...]   # state-machine rule
# … 100+ lines of these
```

**(b) App-managed runtime values** — written by the *user* through the
iOS app when they toggle AI features in Settings. Two specific lines:
```python
IS_ENABNLED = True            # is this AI feature turned on?
CAMERA_NAME = 'front-door-7c7072'   # which camera does this AI watch?
```
(`IS_ENABNLED` — yes, mis-spelled in the existing codebase — left as-is
to avoid breaking everything that already references it.)

The hub-controller writes the (b) lines **directly into the same file**
whenever the user toggles in the app. The function that does this is
`camera.tasks.camera_setting_config()` in `camera/tasks.py:217`.

### 2.4 The bug we're working around

When an AI Docker image is rebuilt and the bind-mounted host file is
overwritten with a fresh source from the AI repo, the (a) developer
constants arrive correctly — that's the whole point of the rebuild — but
the (b) app-managed values get **clobbered back to defaults**:
```python
IS_ENABNLED = False    # default — feature is OFF
CAMERA_NAME = ''       # default — no camera bound
```
The container starts up, reads "feature is off", and goes to sleep. The
user has to open the app, toggle the AI feature off and on again to
restore it. This is annoying and easy to forget after a deploy.

The proper architectural fix is to split the file into two — developer
defaults vs app-managed runtime — so deploys can never overwrite the
user's settings. We've **deliberately deferred that** because it would
require coordinated changes across all four AI repos, the hub-controller
writer, the gold-image bake, and the docker-compose. Instead we use the
healer below as a sustained band-aid.

---

## 3. What the healer does (the actual fix)

### 3.1 In one sentence

A Celery beat task (`camera.tasks.heal_ai_constants`) runs every 60 seconds
on the hub, reads the canonical desired state from the Django
`camera.CameraSetting` singleton, compares it to what's on disk in each
AI's bind-mounted `constants.py`, and re-applies any drift via the
existing `camera_setting_config()` writer.

### 3.2 Concretely, every 60 seconds:

1. Open the Django ORM, fetch the `CameraSetting` row (singleton):
   ```python
   cs = CameraSetting.objects.first()   # may be None on a fresh hub
   ```
   This row holds the user's desired AI feature toggles plus camera bindings:
   - `cs.license_vehicle_recognition` (bool) → desired `IS_ENABNLED` for VehicleAI
   - `cs.vehicle_recognition_camera` (FK to Camera) → desired `CAMERA_NAME` for VehicleAI
   - …same pattern for parcel/face/sound

2. For each of the 4 AI containers (driven by a config table at the top of
   `camera/tasks.py:AI_HEALER_CONTAINERS` — no hardcoded paths in the task
   body, every path is a Django setting from `settings.local.py`):
   - Open the host's `constants.py`, parse the `IS_ENABNLED` and
     `CAMERA_NAME` lines.
   - Compare to the values from `cs` above.
   - If they match → nothing to do, log debug, move on.
   - If they don't match → drift. Call the existing
     `camera_setting_config()` function with the desired values. That
     function does `chattr -i` → `sed` two lines → `chattr +i` →
     restart the container. Same code the user-toggle handler runs in
     the app — just kicked by a clock instead of a click.

3. Log a single summary line: `healed=N skipped=M total=4`.

### 3.3 Files involved (full paths)

On developer machines (this repo):
- `/Users/topsycombs/jupytertemi/jupyter-hub-controller/camera/tasks.py`  
  — contains `heal_ai_constants()` task and `AI_HEALER_CONTAINERS` config table.
- `/Users/topsycombs/jupytertemi/jupyter-hub-controller/hub_controller/settings/common.py`  
  — registers the task in `CELERY_BEAT_SCHEDULE` (60 s default; overridable via env `AI_HEALER_INTERVAL_S`) and routes it to the existing `camera_queue`.
- `/Users/topsycombs/jupytertemi/jupyter-hub-controller/docs/ai-constants-healer.md`  
  — this document.

On a deployed hub:
- `/root/jupyter-hub-controller/camera/tasks.py` (same source as above, deployed)
- `/root/jupyter-hub-controller/hub_controller/settings/common.py`
- The systemd services that run it: `jupyter-hub-celery-beat.service` (the scheduler) and `jupyter-hub-celery-camera.service` (the worker that executes the task).

---

## 4. Behaviour during onboarding (no action required)

**Onboarding** = the very first time a hub is connected to a user account
through the iOS app's onboarding wizard, OR after a reset.

1. **Right after a fresh flash, before the user opens the app:**
   - The Celery beat scheduler is running.
   - The healer task fires every 60 s.
   - It tries to fetch `CameraSetting.objects.first()` and gets `None`
     (no row yet — the user hasn't configured anything).
   - It logs `"[ai-healer] No CameraSetting row yet (pre-onboarding) — skipping"`
     and exits with return value `"no-camera-setting"`.
   - **No bind-mounted file is touched.** The AI containers run with their
     default `IS_ENABNLED = False` from the developer-shipped constants —
     correct, because no user has enabled anything yet.

2. **User completes app onboarding, opens AI Settings, toggles VehicleAI on
   and selects a camera:**
   - The iOS app sends a PATCH to the hub-controller's CameraSetting endpoint.
   - `CameraSettingsManager.update_setting` saves the row, then calls
     `handle_license_vehicle_recognition()` which calls
     `camera_setting_config.apply_async(...)`.
   - That Celery task writes the bind-mounted constants.py with
     `IS_ENABNLED = True` and `CAMERA_NAME = '<chosen-cam-slug>'`,
     then restarts the AI container. **All of this is existing code,
     unchanged by the healer.**
   - Within the next 60 s the healer runs, finds on-disk values match
     desired values → no drift → no action. Quiet steady state.

3. **From now on:** the healer is just a passive guard. It runs forever,
   does nothing 99% of the time, and acts only when something (an image
   rebuild, an OTA, a stray manual edit) breaks the bind-mount.

**Action required during onboarding: NONE.**

---

## 5. Behaviour during offboarding (no action required)

**Offboarding** = the user removes their hub via the app, or operator runs
`reset_hub.sh`.

1. `reset_hub.sh` Phase 3 stops the AI containers and the hub-controller's
   Celery services. The healer is paused.

2. `reset_hub.sh` Phase 4–5 wipes user-specific Django data (the
   `CameraSetting` row goes with it).

3. `reset_hub.sh` Phase 6 re-enables and restarts the systemd services for
   the hub-controller, including `jupyter-hub-celery-beat.service` and
   `jupyter-hub-celery-camera.service`.

4. The healer task fires within the next 60 s, finds no `CameraSetting`
   row, logs `"pre-onboarding"`, and exits. The bind-mounted constants.py
   files keep whatever values they had before the reset — but no AI
   containers are running anyway, so this doesn't matter. Nothing breaks.

5. When a new user onboards (same as section 4 above), the healer
   transitions cleanly into the normal post-onboard steady state.

**Action required during offboarding: NONE.**

---

## 6. Why no action is ever needed (the design promise)

- The healer **code** lives at `/root/jupyter-hub-controller/camera/tasks.py`
  — version-controlled, gold-image-baked. Identical on every hub. **No
  per-hub state files.**
- The beat **schedule entry** lives at
  `/root/jupyter-hub-controller/hub_controller/settings/common.py` —
  same file every other beat task lives in. **No new systemd units, no
  new /etc files.**
- The **canonical state** is Django's `CameraSetting` singleton — the
  same model the app already reads/writes through the existing endpoint.
  **No new models, no new tables, no new APIs.**
- The **writer** is the existing `camera_setting_config()` function that
  already runs whenever the user toggles in the app. **No new disk
  writers.**
- The **interval** is `AI_HEALER_INTERVAL_S` env var (default 60). If you
  ever want to change it, set the env var, restart the beat service. No
  code change needed.
- The **container coverage** is `camera.tasks.AI_HEALER_CONTAINERS` — a
  small list of dicts at the top of the task module. To add a new AI
  service, append one dict and add the matching path setting in
  `local.py`. No invasive changes.

---

## 7. Verification — confirm it works on any hub

Copy-paste these commands on the hub. If they don't behave as described,
something's broken.

```bash
# 1. Confirm the schedule is registered:
grep heal-ai-constants \
  /root/jupyter-hub-controller/hub_controller/settings/common.py
# expected: a line containing "task": "camera.tasks.heal_ai_constants"

# 2. Tail the worker that executes the task:
journalctl -u jupyter-hub-celery-camera -f | grep ai-healer

# (open another shell — we're going to manually break the bind-mount)

# 3. Force drift on VehicleAI:
sudo chattr -i /root/jupyter-container/pilot_vehicle_ai/constants.py
sudo sed -i "s|^IS_ENABNLED = .*|IS_ENABNLED = False|" \
  /root/jupyter-container/pilot_vehicle_ai/constants.py
sudo chattr +i /root/jupyter-container/pilot_vehicle_ai/constants.py

# 4. Verify drift applied:
grep '^IS_ENABNLED' /root/jupyter-container/pilot_vehicle_ai/constants.py
# expected: IS_ENABNLED = False

# 5. Wait up to 60 seconds for the next beat tick.

# 6. Check the file again:
grep '^IS_ENABNLED' /root/jupyter-container/pilot_vehicle_ai/constants.py
# expected: IS_ENABNLED = True (auto-restored — IF the user has Vehicle AI
#          enabled in the app for this hub. If not, the desired value is
#          False and the healer correctly leaves it that way.)

# 7. Check log for the heal event:
journalctl -u jupyter-hub-celery-camera --since='2 minutes ago' \
  | grep ai-healer
# expected: a line like
#   [ai-healer] vehicle drift detected — on-disk=(IS_ENABNLED=False, ...)
#               desired=(IS_ENABNLED=True, CAMERA_NAME='...') → re-applying
#   ... and one like:
#   Task camera.tasks.heal_ai_constants[...] succeeded in N s:
#     'healed=1 skipped=0 total=4'
```

A live receipt from Vancouver hub on 2026-04-30 deploying this:

```
=== T0 (initial state, healer at rest):
IS_ENABNLED = True
CAMERA_NAME = 'front-door-7c7072'

=== T1 (drift induced manually):
IS_ENABNLED = False
CAMERA_NAME = ''

=== T2 (70 seconds later, no operator action):
IS_ENABNLED = True
CAMERA_NAME = 'ring-doorbell-6d2ad3'   ← restored from Django state

[ai-healer] vehicle drift detected — on-disk=(IS_ENABNLED=False, CAMERA_NAME='')
  desired=(IS_ENABNLED=True, CAMERA_NAME='ring-doorbell-6d2ad3') → re-applying
Task camera.tasks.heal_ai_constants succeeded in 12.7 s: 'healed=1 skipped=0 total=4'
```

---

## 8. Failure modes & what to do

| Symptom | Likely cause | Fix |
|---|---|---|
| Healer never fires | `jupyter-hub-celery-beat` service down | `systemctl restart jupyter-hub-celery-beat` |
| Healer fires but never finds drift | All `CameraSetting` values match disk; this is normal | Nothing — quiet steady state |
| Healer logs `"pre-onboarding"` forever | `CameraSetting` row never created — user hasn't onboarded yet, or the onboarding endpoint failed | Have the user complete app onboarding |
| Healer logs `"bind-mount missing"` for a specific AI | The corresponding host file under `/root/jupyter-container/.../constants.py` doesn't exist | Re-deploy that AI container — the bind-mount file is created during the normal compose-up flow |
| Healer crashes with traceback | Bug — file an issue | Check `journalctl -u jupyter-hub-celery-camera`, capture full traceback, ping the AI team |
| `CameraSetting` row exists, healer thinks it doesn't | Database connection issue | Check that `jupyter-hub-controller` and `postgres` containers are both running and the `.env` `DB_PASSWORD` matches |

---

## 9. Future architectural fix (not your concern today)

The proper long-term fix is to split each AI's `constants.py` into two
files:
- `constants.py` — developer code-level defaults, owned by the AI repo,
  baked into the Docker image, never bind-mounted.
- `constants_runtime.py` — only the app-managed lines (`IS_ENABNLED`,
  `CAMERA_NAME`), bind-mounted from the host, owned by the hub-controller.

`constants.py` would `from constants_runtime import *` at the top to pick
up the runtime values. That way, image rebuilds **physically can't**
clobber the runtime values, and the healer becomes redundant and gets
removed.

This requires coordinated changes across **four AI repos**
(VehicleAI / FaceAI / ParcelAI / UnusualSoundsAI), the hub-controller
writer, the gold-image bake, and the docker-compose mounts. We've
deliberately deferred it. If/when it lands, the healer task and the
beat-schedule entry can be removed in a single commit.

Until then: this healer, no operator action ever required.
