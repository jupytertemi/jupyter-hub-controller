import logging
import time

import docker
from docker.errors import APIError, NotFound

from core.manage_service import control_service, get_service_status
import subprocess


def restart_service(container_name: str):
    # Restarting ai service
    # try:
    #     client = docker.from_env()
    #     # Find the container by name
    #     container = client.containers.get(container_name)
    #     # Restart the container
    #     container.restart()
    #     logging.info(f"Restart {container_name} service successfully")
    # except NotFound:
    #     logging.error(
    #         f'Error: Container "{container_name}" not found. Please check the container name.'
    #     )
    
    compose_dir = "/root/jupyter-container"

    try:
        logging.info("Stopping service...")

        subprocess.run(
            ["docker", "compose", "down", container_name],
            cwd=compose_dir,
            check=True
        )

        logging.info("Starting service again...")

        subprocess.run(
            ["docker", "compose", "up", "-d", container_name],
            cwd=compose_dir,
            check=True
        )

        logging.info(f"Service {container_name} restarted successfully")

    except subprocess.CalledProcessError as e:
        logging.error(f"Restart service failed: {e}")


def stop_service(container_name: str):
    try:
        client = docker.from_env()
        container = client.containers.get(container_name)

        container.reload()

        if container.status != "running":
            logging.info(f"{container_name} already stopped — skip stop")
            return

        container.stop()
        logging.info(f"Stopped {container_name}")

    except NotFound:
        logging.error(f'Container "{container_name}" not found')
    except APIError as e:
        logging.error(f"Docker API error: {e}")


def start_service(container_name: str):
    try:
        client = docker.from_env()
        container = client.containers.get(container_name)

        container.reload()

        if container.status == "running":
            logging.info(f"{container_name} already running — skip start")
            return

        container.start()
        logging.info(f"Started {container_name}")

    except NotFound:
        logging.error(f'Container "{container_name}" not found')
    except APIError as e:
        logging.error(f"Docker API error: {e}")


def restart_system_service(service_name):
    status_info = get_service_status(service_name)
    if status_info == "processing":
        timeout_value = 0
        while True:
            status_info = get_service_status(service_name)
            if status_info in ["processing"]:
                timeout_value += 1
                time.sleep(1)
            elif status_info not in ["processing"]:
                break
            elif timeout_value > 5:
                break
    if status_info != "running":
        control_service(action="stop", service_name=service_name)

        control_service(action="start", service_name=service_name)
    else:
        control_service(action="restart", service_name=service_name)
