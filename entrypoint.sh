#!/bin/bash

echo "Starting Gunicorn in background"
gunicorn --bind 0.0.0.0:8000 --timeout 300 --access-logfile - hub_controller.wsgi