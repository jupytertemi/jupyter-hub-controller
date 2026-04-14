import logging

import requests
from django.conf import settings

from .check_hub_mac_address import get_serial_number
from .token_generate import generate_basic_token

logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
)


class APIClient:
    HOST = settings.JUPYTER_HOST
    HUB_CREDENTIAL_URL = "/hub/credential"
    HUB_HOST_URL = "/hub/host"
    TURNS_CREDENTIAL_REVOKE_URL = "/turns-credentials/{serial}/revoke"

    def getHubCredentials(self, slug_name, hub_secret):
        try:
            headers = {
                "Content-type": "application/json",
                "Authorization": generate_basic_token(
                    username=slug_name, password=hub_secret
                ),
            }
            params = {}
            r = requests.get(
                url=self.HOST + self.HUB_CREDENTIAL_URL,
                headers=headers,
                params=params,
            )
            logging.info(f"Get credentials request status {r.status_code}")
            if r.status_code == 200:
                return r.json(), True
            logging.error(f"Get credentials fail response {r}")
            logging.error("Get credential fail")
            return {}, False
        except requests.exceptions.ConnectionError as e:
            logging.error(f"Error executing get credential: {e}")
            return {}, False
        except Exception as e:
            logging.error(f"Error executing get credential: {e}")
            return {}, False

    def setHubHost(self, slug_name, hub_secret, remote_host, local_host, time_zone):
        try:
            headers = {
                "Content-type": "application/json",
                "Authorization": generate_basic_token(
                    username=slug_name, password=hub_secret
                ),
            }
            payload = {
                "remote_host": remote_host,
                "local_host": local_host,
                "time_zone": time_zone,
            }
            params = {}
            r = requests.patch(
                url=self.HOST + self.HUB_HOST_URL,
                headers=headers,
                json=payload,
                params=params,
            )
            logging.info(f"Set host request status {r.status_code}")
            if r.status_code == 200:
                return r.json(), True
            logging.error(f"Set host fail response {r}")
            logging.error("Set host fail")
            return {}, False
        except requests.exceptions.ConnectionError as e:
            logging.error(f"Error executing set host: {e}")
            return {}, False
        except Exception as e:
            logging.error(f"Error executing set host: {e}")
            return {}, False

    def check_health(self):
        try:
            url = "http://localhost:8000/api/health"
            response = requests.get(url)

            # Log the status code and response
            logging.info(f"Status Code health check: {response.status_code}")
            logging.info(f"Response Text health check : {response.text}")

            # If the response is JSON, parse and print it
            try:
                if response.status_code == 200:
                    return True
            except ValueError:
                logging.info("Response health check is not in JSON format.")

            # Return the response object for further use
            return False
        except Exception as e:
            logging.error(f"Error executing health check: {e}")
            return False

    def revokeTurnsCredential(self, slug_name, hub_secret):
        try:
            serial = get_serial_number()
            logging.info(f"Serial number: {serial}")

            headers = {
                "Content-type": "application/json",
                "Authorization": generate_basic_token(
                    username=slug_name, password=hub_secret
                ),
            }

            url = self.HOST + self.TURNS_CREDENTIAL_REVOKE_URL.format(serial=serial)

            r = requests.get(
                url=url,
                headers=headers,
            )

            logging.info(f"Revoke turns credential [{serial}] status {r.status_code}")

            if r.status_code == 200:
                return r.json(), True

            logging.error(
                f"Revoke turns credential fail response {r.status_code} - {r.text}"
            )
            return {}, False

        except requests.exceptions.ConnectionError as e:
            logging.error(f"Connection error revoke turns credential: {e}")
            return {}, False
        except Exception as e:
            logging.error(f"Error executing revoke turns credential: {e}")
            return {}, False
