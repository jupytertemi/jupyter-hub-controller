import logging
import os
import shutil

from django.conf import settings


def copy_file(file_path, file_name):
    try:
        backup_file_path = file_path.replace(file_name, f"{file_name}_backup")
        shutil.copyfile(file_path, backup_file_path)
        logging.info(f"File {file_path} has been copied to {backup_file_path}.")
    except Exception as e:
        logging.error(f"Error copying file: {e}")


def sync_env_file(file_path):
    try:
        backup_file_path = "/root/jupyter-container/.env"
        shutil.copyfile(file_path, backup_file_path)
        logging.info(f"File {file_path} has been copied to {backup_file_path}.")
    except Exception as e:
        logging.error(f"Error copying file: {e}")


def read_env_file(key, env_path=settings.ENV_FILE):
    try:
        if not os.path.isfile(env_path):
            logging.info(f"File {env_path} not exist. Read env file fail.")
            return None
        else:
            with open(env_path, "r") as f:
                lines = f.readlines()

            for line in lines:
                if line.startswith(f"{key}="):
                    value = line.replace(f"{key}=", "")
                    if value.isspace():
                        return None
                    return value.replace("\n", "")
            return None
    except Exception as e:
        logging.error(f"Read env file error: {e}")


def update_env_value(key, value, env_path=settings.ENV_FILE):
    try:
        env_val = read_env_file(key)
        logging.info(f"Current {key} = {value} in {env_path}.")

        if env_val != value:
            if not os.path.isfile(env_path):
                logging.info(f"File {env_path} not exist. Creating file...")
                with open(env_path, "w") as f:
                    f.write(f"{key}={value}\n")
            else:
                with open(env_path, "r") as f:
                    lines = f.readlines()

                found = False
                with open(env_path, "w") as f:
                    for line in lines:
                        if line.startswith(f"{key}="):
                            f.write(f"{key}={value}\n")
                            found = True
                        else:
                            if not line.isspace():
                                f.write(line)
                    if not found:
                        f.write(f"{key}={value}\n")
                logging.info(f"Update {key} to {value} in {env_path}.")
            if env_path == settings.ENV_FILE:
                sync_env_file(env_path)
            return False
        else:
            return True
    except Exception as e:
        logging.error(f"Update env file error: {e}")
        return False
