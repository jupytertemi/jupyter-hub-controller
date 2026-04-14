import json
import logging
import socket
import time
from typing import Optional

SOCKET_HOST = "localhost"
SOCKET_PORT = 4444


def _wait_for_response(
    sock: socket.socket, payload: dict, timeout: int
) -> Optional[dict]:
    sock.settimeout(timeout)
    stream = sock.makefile("r")
    expected_action = payload.get("action")
    expected_mac = payload.get("mac")

    while True:
        line = stream.readline()
        if not line:
            return None

        try:
            data = json.loads(line.strip())
        except json.JSONDecodeError:
            logging.warning(f"Invalid socket response: {line.strip()}")
            continue

        response_action = data.get("action")
        response_mac = data.get("mac")
        if response_action == expected_action and (
            not expected_mac or response_mac == expected_mac
        ):
            return data


def publish_socket_message(
    payload: dict, wait_response: bool = False, timeout: int = 15
):
    try:
        with socket.create_connection((SOCKET_HOST, SOCKET_PORT), timeout=3) as sock:
            reg_payload = {
                "action": "register",
                "device": "python-app-controller",
                "type": "app",
            }
            msg_reg = json.dumps(reg_payload) + "\n"
            sock.sendall(msg_reg.encode())
            logging.info(f"Sent Register: {msg_reg.strip()}")

            time.sleep(0.5)
            message = json.dumps(payload) + "\n"
            sock.sendall(message.encode())
            logging.info(f"Sent Payload: {message.strip()}")

            if wait_response:
                response = _wait_for_response(sock, payload, timeout=timeout)
                if response:
                    logging.info(f"Socket response: {response}")
                return response

    except Exception as e:
        logging.warning(f"Socket publish failed: {e}")

    return None
