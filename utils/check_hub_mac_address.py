import logging

logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
)


def get_serial_number():
    try:
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if line.lower().startswith("serial"):
                    return line.split(":")[1].strip()
    except Exception as e:
        logging.error(f"Error get serial number: {e}")
        return None
