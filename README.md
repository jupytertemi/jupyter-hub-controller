# Jupyter Hub Controller

Django backend for Jupyter Smart Home Hub - manages Home Assistant, ESPHome devices, cameras, and security integrations.

## Recent Fixes (April 2026)

### Halo Onboarding Fix

**Problem:** Alarm device onboarding failed when ESPHome devices were auto-discovered via MQTT instead of API.

**Root Cause:**
- `AlarmDeviceManager.create()` tried to add device via `add_esphome_device_by_name()`
- Failed with `socket.gaierror` when device already existed (auto-discovered)
- Didn't enable `allow_service_calls` (required for MQTT publishing)

**Solution:**
1. Check for existing HA entry first (`find_esphome_entry_id()`)
2. Only attempt API add if not found
3. Auto-enable `allow_service_calls` during onboarding
4. Made delete resilient (cleanup even if factory reset fails)

**Files to Update:**
- `alarm/managers.py` - AlarmDeviceManager.create()
- `alarm/views.py` - RetrieveDeleteAlarmDeviceView.destroy()
- `utils/hass_client.py` - Add enable_service_calls() method

See Fortress hub (192.168.1.119) for implementation.

## Full Code

Full Django codebase to be pushed from hub deployment.

## License

Proprietary - Jupyter Devices
