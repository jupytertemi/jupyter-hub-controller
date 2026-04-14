#!/bin/bash

sudo chmod +x /root/jupyter-container/start_hub_controller.sh

# Set systemd service
echo "[Unit]
Description=Jupyter Hub.

[Service]
Type=simple
WorkingDirectory=/root/jupyter-container
EnvironmentFile=/root/jupyter-container/.env
ExecStart=/root/jupyter-container/start_hub_controller.sh
Wants=jupyter-hub-celery.service
Restart=no

[Install]
WantedBy=multi-user.target" | sudo tee /etc/systemd/system/jupyter-hub-controller.service > /dev/null

# Reload systemd manager configuration
sudo systemctl daemon-reload

# Set systemd service for Celery worker
echo "[Unit]
Description=Celery Starting Celery Worker Camera
After=network.target jupyter-hub-controller.service
PartOf=jupyter-hub-controller.service

[Service]
Type=simple
WorkingDirectory=/root/jupyter-hub-controller
EnvironmentFile=/root/jupyter-hub-controller/.env
Environment=\"PATH=/root/jupyter-hub-controller/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\"
ExecStart=/root/jupyter-hub-controller/.venv/bin/celery -A hub_controller worker --loglevel=info --queues=camera_queue,loitering_queue,parcel_detect_queue --concurrency=1 --hostname=worker_camera@%h --max-memory-per-child=500
Restart=always

[Install]
WantedBy=jupyter-hub-controller.service" | sudo tee /etc/systemd/system/jupyter-hub-celery-camera.service > /dev/null


echo "[Unit]
Description=Celery Starting Celery Worker Automation
After=network.target jupyter-hub-controller.service
PartOf=jupyter-hub-controller.service

[Service]
Type=simple
WorkingDirectory=/root/jupyter-hub-controller
EnvironmentFile=/root/jupyter-hub-controller/.env
Environment=\"PATH=/root/jupyter-hub-controller/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\"
ExecStart=/root/jupyter-hub-controller/.venv/bin/celery -A hub_controller worker --loglevel=info --queues=facial_queue,automation_queue,hub_operations_queue --concurrency=1 --hostname=worker_automation@%h --max-memory-per-child=500
Restart=always

[Install]
WantedBy=jupyter-hub-controller.service" | sudo tee /etc/systemd/system/jupyter-hub-celery-automation.service > /dev/null


echo "[Unit]
Description=Celery Starting Celery Restart Ring Safe
After=network.target jupyter-hub-controller.service
PartOf=jupyter-hub-controller.service

[Service]
Type=simple
WorkingDirectory=/root/jupyter-hub-controller
EnvironmentFile=/root/jupyter-hub-controller/.env
Environment=\"PATH=/root/jupyter-hub-controller/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\"
ExecStart=/root/jupyter-hub-controller/.venv/bin/celery -A hub_controller worker --loglevel=info --queues=ring_safe_queue --concurrency=1 --hostname=worker_ring_safe@%h --max-memory-per-child=500
Restart=always

[Install]
WantedBy=jupyter-hub-controller.service" | sudo tee /etc/systemd/system/jupyter-hub-celery-restart-ring.service > /dev/null


echo "[Unit]
Description=Celery Starting Celery  Beat
After=network.target jupyter-hub-controller.service
PartOf=jupyter-hub-controller.service

[Service]
Type=simple
WorkingDirectory=/root/jupyter-hub-controller
EnvironmentFile=/root/jupyter-hub-controller/.env
Environment=\"PATH=/root/jupyter-hub-controller/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\"
ExecStart=/root/jupyter-hub-controller/.venv/bin/celery -A hub_controller beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler
Restart=always

[Install]
WantedBy=jupyter-hub-controller.service" | sudo tee /etc/systemd/system/jupyter-hub-celery-beat.service > /dev/null
# Reload systemd manager configuration
sudo systemctl daemon-reload

# Enable and start service
sudo systemctl enable jupyter-hub-celery-beat.service
sudo systemctl enable jupyter-hub-celery-restart-ring.service
sudo systemctl enable jupyter-hub-celery-automation.service
sudo systemctl enable jupyter-hub-celery-camera.service

sudo chmod +x /root/reset_hub.sh

# Set systemd reset service
echo "[Unit]
Description=Jupyter Hub reset.

[Service]
Type=simple
ExecStart=/bin/bash -c \"cd /root && ./reset_hub.sh\"
Restart=no

[Install]
WantedBy=multi-user.target" | sudo tee /etc/systemd/system/jupyter-hub-reset.service > /dev/null

# Reload systemd manager configuration
sudo systemctl daemon-reload

echo "Setup complete!"

exit 0
