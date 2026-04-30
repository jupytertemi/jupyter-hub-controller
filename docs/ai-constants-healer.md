# AI Constants Healer — operator notes for offboarding / onboarding

## What this is

A periodic Celery beat task at `camera.tasks.heal_ai_constants`. Runs every
60 seconds (env-tunable via `AI_HEALER_INTERVAL_S`). Its job is to keep the
two **app-managed** lines in each AI Docker container's bind-mounted
`constants.py` in sync with the canonical state in the Django
`camera.CameraSetting` singleton.

The two app-managed lines are:
- `IS_ENABNLED = True | False`
- `CAMERA_NAME = '<slug>'`

These are the lines that the existing `camera.tasks.camera_setting_config`
writes to disk whenever a user toggles an AI feature in the app. They live
in the same file as the developer code-level constants (thresholds, model
paths) — which means an AI image rebuild that copies a fresh source over
the bind-mount can clobber them. The healer detects that drift and
re-applies the correct values automatically.

## Containers covered

Driven by `camera.tasks.AI_HEALER_CONTAINERS` (no hardcoded paths in this
module — every path comes from `settings.local.py`, env-overridable):

| Container | CameraSetting field | Camera FK | Path setting |
|---|---|---|---|
| number_plate_detection | `license_vehicle_recognition` | `vehicle_recognition_camera` | `VEHICLE_CONFIG_PATH` |
| parcel_detection | `enable_parcel_detect` | `parcel_detect_camera` | `PARCEL_CONFIG_PATH` |
| face_recognition | `enable_face_recognition` | (none) | `FACIAL_CONFIG_PATH` |
| sound_detection | `activate_sounds_detection` | (none) | `SOUND_DETECTION_PATH` |

To add a new AI: append to `AI_HEALER_CONTAINERS` and add the corresponding
path setting in `local.py`. No other code changes needed.

## Cadence + tunables

| Setting | Default | Override |
|---|---|---|
| Beat interval | 60 s | `AI_HEALER_INTERVAL_S` env var |
| Per-AI bind-mount path | `settings.<X>_CONFIG_PATH` | env var per `local.py` |
| Per-AI container name | `settings.<X>_CONTAINER_NAME` | env var per `local.py` |

## Onboarding behaviour

A fresh hub does NOT have a `CameraSetting` row until the user opens the
app's AI settings page (or the first PATCH to the settings endpoint creates
one). Until that point the healer logs `"No CameraSetting row yet
(pre-onboarding) — skipping"` and does nothing — correct behaviour.

After onboarding:
1. User toggles AI features in the app → cloud → hub-controller PATCH
2. `CameraSettingsManager.update_setting` writes the row + calls
   `camera_setting_config` to update the bind-mounted constants
3. The healer (running every 60 s) confirms the bind-mount matches; no
   drift, no action — just a quiet log line

## Offboarding behaviour

`reset_hub.sh` wipes user data. After offboard:
- `CameraSetting` row may be deleted along with the rest of the Django data
- AI containers are stopped (per `reset_hub.sh` Phase 3)
- The healer keeps running (Celery beat is restarted in Phase 6) but finds
  no `CameraSetting` row → no-ops with `"pre-onboarding"` log line until
  the next user onboards

The healer code itself — both `camera/tasks.py` and the beat schedule entry
in `hub_controller/settings/common.py` — lives in version-controlled hub
source under `/root/jupyter-hub-controller/`. It survives offboarding and
onboarding without any explicit handling because:
- It's loaded from disk by Celery beat at every service start
- No hub-specific state files
- No /etc files added
- No systemd units added

**Backend dev: no action needed for offboard/onboard.** It Just Works.

## What if the healer crashes?

Wrapped in try/except per AI. If one AI's heal fails, the others still
run. Failures log to the standard hub-controller log path. If the entire
task crashes, Celery beat restarts it on the next interval — at most one
60 s interval is missed.

## How to verify it's working

On any onboarded hub:

```bash
# 1. Check beat schedule includes the healer
grep heal-ai-constants /root/jupyter-hub-controller/hub_controller/settings/common.py

# 2. Tail the celery beat / camera-queue worker
journalctl -u jupyter-hub-celery-camera -f | grep ai-healer

# 3. Force drift and watch it heal
sudo chattr -i /root/jupyter-container/pilot_vehicle_ai/constants.py
sudo sed -i "s|^IS_ENABNLED = .*|IS_ENABNLED = False|" /root/jupyter-container/pilot_vehicle_ai/constants.py
sudo chattr +i /root/jupyter-container/pilot_vehicle_ai/constants.py

# Wait up to 60 s
grep "^IS_ENABNLED" /root/jupyter-container/pilot_vehicle_ai/constants.py
# expected: IS_ENABNLED = True (auto-restored by the next beat tick)
```

## Why this is the right band-aid

- No new architecture: same file format, same writer (`camera_setting_config`),
  same `CameraSetting` model.
- No new state files, no new systemd units, no new bind-mounts.
- One source of truth for desired state: Django.
- Self-heals all AI containers in one pass.
- Works after image rebuilds, after hub reboots, after hub-manager OTAs.
- Operator never types a command.

A cleaner long-term fix would split `constants.py` into developer-defaults
+ app-managed-runtime files, so deploys can never overwrite the latter.
That's an architecture change. This healer buys us time and is itself
trivially removable when that refactor lands.
