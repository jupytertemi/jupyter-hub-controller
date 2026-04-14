# Jupyter Hub Controller

Django-based backend for managing Jupyter Smart Home Hub.

## Features

- **Home Assistant Integration**: Manage ESPHome devices, automations, and scripts
- **Halo Alarm Management**: Onboard and control Jupyter Halo smart speakers
- **Camera Management**: RTSP camera discovery, Ring integration, zone configuration
- **Event Management**: Face recognition, vehicle detection, parcel theft, loitering alerts
- **Automation**: Garage door control, alarm modes, occupancy illusion
- **Cloud Backup**: Google Drive integration for recordings

## Recent Fixes (April 2026)

### Halo Onboarding Fix
**Problem:** Onboarding failed with socket.gaierror when device was auto-discovered via MQTT.

**Solution:**
1. Check for existing HA entry first (find_esphome_entry_id)
2. Only try add_esphome_device_by_name if not found
3. Auto-enable allow_service_calls during onboarding
4. Make delete operation resilient (cleanup even if factory reset fails)

**Files changed:**
- alarm/managers.py - AlarmDeviceManager.create()
- alarm/views.py - RetrieveDeleteAlarmDeviceView.destroy()
- utils/hass_client.py - Added enable_service_calls()

## Setup

See documentation for installation instructions.

## License

Proprietary - Jupyter Devices
