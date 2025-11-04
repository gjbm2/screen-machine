#!/bin/bash
# Reboot the server with sudo password

echo "Rebooting server..."
echo "${MEDIA_SERVER_PASSWORD}" | sudo -S reboot

