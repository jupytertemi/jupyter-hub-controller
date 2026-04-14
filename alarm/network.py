"""Network utility functions for IP and MAC address operations."""
import re
import subprocess
import logging


def ping_host(ip, timeout=1):
    """
    Ping a host to check if it's reachable.
    
    Args:
        ip: IP address to ping
        timeout: Timeout in seconds (default 1)
    
    Returns:
        bool: True if host is reachable, False otherwise
    """
    try:
        result = subprocess.run(
            ['ping', '-c', '1', '-W', str(timeout), ip],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout + 1
        )
        return result.returncode == 0
    except Exception as e:
        logging.warning(f"Ping failed for {ip}: {e}")
        return False


def get_mac_address(ip):
    """
    Get MAC address from ARP table for a given IP.
    
    Args:
        ip: IP address to look up
    
    Returns:
        str: MAC address in format 'aa:bb:cc:dd:ee:ff', or None if not found
    """
    try:
        # Try to ping first to ensure ARP entry exists
        ping_host(ip, timeout=1)
        
        # Read ARP table
        result = subprocess.run(
            ['arp', '-n', ip],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        # Parse output for MAC address (format: aa:bb:cc:dd:ee:ff or aa-bb-cc-dd-ee-ff)
        mac_pattern = r'([0-9a-f]{2}[:\-]){5}([0-9a-f]{2})'
        match = re.search(mac_pattern, result.stdout, re.IGNORECASE)
        
        if match:
            mac = match.group(0).lower().replace('-', ':')
            logging.debug(f"Found MAC {mac} for IP {ip}")
            return mac
        
        logging.warning(f"No MAC found in ARP table for {ip}")
        return None
        
    except Exception as e:
        logging.error(f"Failed to get MAC for {ip}: {e}")
        return None


def find_ip_by_mac(mac, populate_arp=False):
    """
    Find IP address by MAC address in ARP table.
    
    Args:
        mac: MAC address to search for (format: 'aa:bb:cc:dd:ee:ff')
        populate_arp: If True, do ARP sweep to populate table before searching
    
    Returns:
        str: IP address if found, None otherwise
    """
    try:
        # Normalize MAC address format
        mac = mac.lower().replace('-', ':')
        
        # Optional ARP sweep to populate table
        if populate_arp:
            logging.info(f"Performing ARP sweep to find MAC {mac}")
            # Get local network range from ip route
            route_result = subprocess.run(
                ['ip', 'route', 'show'],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            # Find local network (e.g., 192.168.1.0/24)
            network_pattern = r'(\d+\.\d+\.\d+)\.0/24'
            match = re.search(network_pattern, route_result.stdout)
            
            if match:
                network_prefix = match.group(1)
                # Ping sweep (async, don't wait for responses)
                subprocess.run(
                    ['nmap', '-sn', f'{network_prefix}.0/24'],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=30
                )
        
        # Read full ARP table
        result = subprocess.run(
            ['arp', '-n'],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        # Parse ARP table for matching MAC
        for line in result.stdout.split('\n'):
            if mac in line.lower():
                # Extract IP address (first field)
                ip_pattern = r'(\d+\.\d+\.\d+\.\d+)'
                ip_match = re.search(ip_pattern, line)
                if ip_match:
                    ip = ip_match.group(1)
                    logging.info(f"Found IP {ip} for MAC {mac}")
                    return ip
        
        logging.warning(f"No IP found for MAC {mac}")
        return None
        
    except Exception as e:
        logging.error(f"Failed to find IP for MAC {mac}: {e}")
        return None
