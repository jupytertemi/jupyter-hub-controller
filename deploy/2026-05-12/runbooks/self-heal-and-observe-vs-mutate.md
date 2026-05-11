# Self-heal + observe-vs-mutate (2026-05-12)

Two structural reliability changes baked together this drop. Both target unattended remote hubs that must self-manage at fleet scale.

## 1. Camera watchdog: observe, never mutate

`camera/tasks.py:monitor_camera_ips` was rewritten to follow the rule:

> Probes observe. Only user actions and definitive identity signals (IP move via MAC follow) mutate streaming config.

Concrete changes:

- ICMP `ping_host` replaced with `alarm.network.tcp_check(host, 554, timeout=3)` — probes the actual service port we care about (RTSP). ICMP false negatives (camera firmware, NAT, dual-interface) no longer trigger destructive actions.
- Removed the auto-disable that flipped `is_enabled=False` after 3 consecutive failures. That flip caused `update_camera_config` to strip cameras from `mediamtx.yml` and `frigate/config.yaml`, turning a transient probe failure into a 15+ minute outage.
- `consecutive_failures` and `last_seen_at` are still tracked for UI/alerting; `camera_offline` MQTT event still fires once when the threshold is crossed. The hub stays observable without being destructive.
- `update_camera_config.delay()` now only fires when a camera's IP genuinely moved (MAC-anchored ARP follow detected new IP). Transient reachability blips never re-render config.

Trust downstream retry. MediaMTX's RTSP source has built-in reconnect with backoff; Frigate's go2rtc has the same. The watchdog should not fight them.

## 2. Boot-time self-heal: one canonical cloudflared owner

`deploy/2026-05-12/{scripts,systemd}/jupyter-hub-self-heal.*`

On every boot, before `cloudflared.service` and `jupyter-hub-controller.service` start, the hub re-converges to the canonical contract:

- `cloudflared.service` is the ONE cloudflared owner (image-baked, `Type=notify`, `Restart=always`, `EnvironmentFile=/root/jupyter-hub-controller/.env`).
- No parallel cloudflared services may exist. `cloudflared-tunnel.service`, `cloudflared-quick.service` are explicitly removed if present.
- `/root/start_cloudflared.sh` (a deprecated helper) must not exist; removed if present.
- Any orphan `cloudflared` process not under the canonical service's cgroup is killed.
- `cloudflared.service` is re-enabled at boot if disabled.

Why this is mandatory at fleet scale: a power cut during reset, a support team applying a manual hotfix, or a partial setup run can all leave drift. Without self-heal, drift is forever. With self-heal, every boot re-converges. Idempotent and safe to run forever.

## The principle behind both

**Subtract, do not add.** When a system seems broken, default to restoring the original mechanism, not creating a parallel one. Parallel mechanisms compete forever and turn into permanent incidents at scale.

Both changes here are subtractions: the camera watchdog removed the destructive action; the self-heal removed parallel cloudflared services. Neither added a new mechanism that the system has to maintain forever.

## Deployment

- Repo commit ships the source files. Image bake step is responsible for installing:
  - `scripts/jupyter-hub-self-heal.sh` → `/usr/local/bin/jupyter-hub-self-heal.sh` (chmod +x)
  - `systemd/jupyter-hub-self-heal.service` → `/etc/systemd/system/jupyter-hub-self-heal.service`
  - `systemctl daemon-reload && systemctl enable jupyter-hub-self-heal.service`
- Python changes hot-reload via gunicorn HUP + `systemctl restart jupyter-hub-celery-camera.service` (the camera worker owns the task).
