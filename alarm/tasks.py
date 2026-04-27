from celery import shared_task

from utils.restarting_service import restart_service, start_service, stop_service


@shared_task
def alarm_unusual_sound_config(
    is_unusual_sound: bool, container_name: str, servicer_path
):
    camera_file_path = servicer_path
    with open(camera_file_path, "r", encoding="UTF-8") as file:
        lines = file.readlines()

    updated_lines = [
        (
            f"STOP_ALARM = {is_unusual_sound}\n"
            if line.strip().startswith("STOP_ALARM")
            else line
        )
        for line in lines
    ]

    # Write and update the file
    with open(camera_file_path, "w", encoding="UTF-8") as file:
        file.writelines(updated_lines)

    restart_service(container_name)
    return f"{container_name} restart config successfully."


@shared_task
def alarm_voice_ai_config(is_unusual_sound: bool, container_name: str):
    if is_unusual_sound is True:
        start_service(container_name)
    else:
        stop_service(container_name)

    restart_service(container_name)
    return f"{container_name} restart config successfully."


@shared_task
def monitor_alarm_ips():
    """
    Monitor alarm device (Halo) IP addresses.
    If unreachable, ARP sweep to find new IP and update hub IP in Halo's NVS.
    """
    import logging
    import requests
    from django.conf import settings
    from alarm.models import AlarmDevice
    from alarm.network import find_ip_by_mac, get_mac_address, ping_host
    
    devices = AlarmDevice.objects.all()
    if not devices.exists():
        return "No alarm devices"
    
    results = []
    hub_ip = None
    
    # Get hub's current IP (from environment or network interface)
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        hub_ip = s.getsockname()[0]
        s.close()
    except Exception as e:
        logging.error(f"Failed to get hub IP: {e}")
        return "Failed to get hub IP"
    
    for device in devices:
        identity = device.identity_name
        
        # Step 1: Backfill MAC if missing
        if not device.mac_address and device.ip_address:
            mac = get_mac_address(device.ip_address)
            if mac:
                device.mac_address = mac
                device.save(update_fields=["mac_address"])
                logging.info(f"Backfilled MAC for {identity}: {mac}")
        
        # Step 2: Ping stored IP
        if device.ip_address and ping_host(device.ip_address):
            results.append(f"{identity}: OK at {device.ip_address}")
            continue
        
        # Step 3: IP unreachable — ARP sweep if we have MAC
        new_ip = None
        if device.mac_address:
            new_ip = find_ip_by_mac(device.mac_address, populate_arp=True)
        
        # Step 4: Found at new IP — update DB and reconfigure Halo's audio settings
        if new_ip and new_ip != device.ip_address:
            old_ip = device.ip_address
            device.ip_address = new_ip
            
            # Backfill MAC if still missing
            if not device.mac_address:
                mac = get_mac_address(new_ip)
                if mac:
                    device.mac_address = mac
            
            update_fields = ["ip_address"]
            if device.mac_address:
                update_fields.append("mac_address")
            
            device.save(update_fields=update_fields)
            
            # Update Halo's NVS with new hub IP via /audiosave endpoint
            try:
                mqtt_port = getattr(settings, 'MQTT_PORT', 5555)
                hub_slug = getattr(settings, "DEVICE_NAME", "")
                url = f"http://{new_ip}/audiosave?local_ip={hub_ip}&port={mqtt_port}&hub_slug={hub_slug}"
                response = requests.get(url, timeout=5)
                if response.status_code == 200:
                    logging.info(f"Updated {identity} audio config at {new_ip}")
                    results.append(f"{identity}: moved {old_ip} -> {new_ip}, audio updated")
                else:
                    logging.warning(f"Failed to update {identity} audio config: {response.status_code}")
                    results.append(f"{identity}: moved {old_ip} -> {new_ip}, audio update failed")
            except Exception as e:
                logging.error(f"Failed to update {identity} audio config: {e}")
                results.append(f"{identity}: moved {old_ip} -> {new_ip}, audio update error")
        
        elif new_ip:
            results.append(f"{identity}: recovered at {new_ip}")
        else:
            logging.warning(f"Alarm device {identity} unreachable")
            results.append(f"{identity}: OFFLINE")
    
    return "; ".join(results)
