#!/bin/bash
# Light Sensor Client Diagnostic Script
# Copy this script to the media server and run it there:
#   scp diagnose-lightsensor.sh user@media-server:/tmp/
#   ssh user@media-server
#   chmod +x /tmp/diagnose-lightsensor.sh
#   /tmp/diagnose-lightsensor.sh > /tmp/lightsensor-report.txt
#   cat /tmp/lightsensor-report.txt

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Light Sensor Client Diagnostic${NC}"
echo -e "${BLUE}  Run on: $(hostname)${NC}"
echo -e "${BLUE}  Date: $(date)${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Function to print section headers
print_section() {
    echo ""
    echo -e "${GREEN}=== $1 ===${NC}"
    echo ""
}

# Function to print findings
print_finding() {
    echo -e "${YELLOW}→${NC} $1"
}

# 1. Search for Python scripts that might handle light sensor
print_section "1. Searching for Light Sensor Python Scripts"

echo "Searching for Python files with 'sensor', 'lux', or 'websocket' keywords..."
find /home /usr/local /opt -name "*.py" -type f 2>/dev/null | while read file; do
    if grep -l -i "sensor\|lux\|yoctopuce\|websocket.*lux\|ws.*send.*lux" "$file" 2>/dev/null; then
        print_finding "Found: $file"
        echo "  File size: $(stat -f%z "$file" 2>/dev/null || stat -c%s "$file" 2>/dev/null) bytes"
        echo "  Modified: $(stat -f%Sm "$file" 2>/dev/null || stat -c%y "$file" 2>/dev/null)"
        echo "  Key lines:"
        grep -n -i "sensor\|lux\|yoctopuce\|websocket" "$file" 2>/dev/null | head -10
        echo ""
    fi
done

# 2. Search for systemd services related to light sensor
print_section "2. Searching for Light Sensor Services"

echo "Checking systemd services..."
systemctl list-unit-files --type=service 2>/dev/null | grep -i "sensor\|lux\|light\|yocto" || echo "No sensor-related services found"
echo ""

echo "Checking ALL systemd service files for sensor/lux/websocket mentions..."
find /etc/systemd/system /lib/systemd/system -name "*.service" -type f 2>/dev/null | while read file; do
    if grep -l -i "sensor\|lux\|yoctopuce\|websocket" "$file" 2>/dev/null; then
        print_finding "Found: $file"
        echo "  Content:"
        cat "$file"
        echo ""
    fi
done

echo "Checking running processes for sensor/websocket..."
ps aux | grep -i "sensor\|lux\|yoctopuce\|python.*ws" | grep -v grep || echo "No sensor-related processes found"
echo ""

# 3. Check for websocket connections
print_section "3. Checking WebSocket Connections"

echo "Looking for ANY websocket connections..."
sudo netstat -tulpn 2>/dev/null | grep -i "websocket\|ws\|8765" || echo "No obvious websocket ports"
echo ""

echo "Checking for connections to common WebSocket ports (8765, 8080, 5000, 8000)..."
for port in 8765 8080 5000 8000; do
    echo "Port $port:"
    sudo lsof -i :$port 2>/dev/null || echo "  No connections"
done
echo ""

# 4. Search for configuration files
print_section "4. Searching for Configuration Files"

echo "Looking for .env files..."
find /home /usr/local /opt -name ".env" -type f 2>/dev/null | while read file; do
    print_finding "Found: $file"
    echo "  Content (with sensitive data visible - review carefully!):"
    cat "$file" 2>/dev/null | head -30
    echo ""
done

echo "Looking for ANY config files mentioning websocket, sensor, or IP addresses..."
find /home /usr/local /opt /etc -type f \( -name "*.conf" -o -name "*.cfg" -o -name "*.ini" -o -name "*.json" -o -name "config.py" \) 2>/dev/null | while read file; do
    if grep -l -i "websocket\|sensor\|lux\|8765\|95\.141\|185\.254" "$file" 2>/dev/null; then
        print_finding "Found: $file"
        grep -n -i "websocket\|sensor\|lux\|8765\|95\.141\|185\.254" "$file" 2>/dev/null | head -10
        echo ""
    fi
done

# 5. Check cron jobs and scheduled tasks
print_section "5. Checking Scheduled Tasks"

echo "User crontabs for all users..."
for user in $(cut -f1 -d: /etc/passwd 2>/dev/null); do
    CRON=$(sudo crontab -u "$user" -l 2>/dev/null | grep -i "sensor\|lux\|python" || true)
    if [ ! -z "$CRON" ]; then
        echo "User: $user"
        echo "$CRON"
        echo ""
    fi
done

echo "System cron jobs..."
grep -r -i "sensor\|lux\|python.*ws" /etc/cron* 2>/dev/null || echo "No relevant cron jobs found"
echo ""

# 6. Check for startup scripts
print_section "6. Checking Startup Scripts"

echo "Checking /etc/rc.local..."
if [ -f /etc/rc.local ]; then
    cat /etc/rc.local
else
    echo "No /etc/rc.local found"
fi
echo ""

echo "Checking user autostart files..."
find /home -path "*/.config/autostart/*" -o -path "*/.bashrc" -o -path "*/.profile" -o -path "*/.bash_profile" 2>/dev/null | while read file; do
    if grep -l -i "sensor\|lux\|python" "$file" 2>/dev/null; then
        print_finding "Found: $file"
        grep -n -i "sensor\|lux\|python" "$file" 2>/dev/null
        echo ""
    fi
done

# 7. Check for USB devices (Yoctopuce sensors use USB)
print_section "7. Checking USB Devices"

echo "USB devices connected..."
lsusb 2>/dev/null || echo "lsusb not available"
echo ""

echo "USB device details..."
ls -la /dev/ttyUSB* /dev/ttyACM* 2>/dev/null || echo "No USB serial devices found"
echo ""

echo "Checking dmesg for USB sensor devices..."
sudo dmesg | grep -i "yoctopuce\|usb.*sensor\|ttyUSB\|ttyACM" | tail -30 || echo "No relevant USB messages"
echo ""

# 8. Check installed Python packages
print_section "8. Checking Python Packages"

echo "Checking for Yoctopuce, websocket, or sensor-related packages..."
for py_cmd in python3 python python3.11 python3.10 python3.9; do
    if command -v $py_cmd &> /dev/null; then
        echo "--- Using: $py_cmd ---"
        $py_cmd --version
        $py_cmd -m pip list 2>/dev/null | grep -i "yocto\|websocket\|sensor\|ws4py\|asyncio" || echo "  No relevant packages found"
        echo ""
    fi
done

# 9. Check for virtual environments
print_section "9. Checking for Virtual Environments"

echo "Looking for Python virtual environments..."
find /home /usr/local /opt -type d -name "venv" -o -name ".venv" -o -name "env" 2>/dev/null | while read venv_dir; do
    print_finding "Found venv: $venv_dir"
    if [ -f "$venv_dir/bin/pip" ]; then
        echo "  Packages in this venv:"
        "$venv_dir/bin/pip" list 2>/dev/null | grep -i "yocto\|websocket\|sensor" || echo "  No relevant packages"
    fi
    echo ""
done

# 10. Check for IP addresses in ALL script files
print_section "10. Checking for Hardcoded IP Addresses"

echo "Looking for IP addresses and WebSocket URLs in ALL script files..."
find /home /usr/local /opt -type f \( -name "*.py" -o -name "*.sh" -o -name "*.js" \) 2>/dev/null | while read file; do
    if grep -l "185\.254\.136\.\|95\.141\.21\.\|ws://\|wss://" "$file" 2>/dev/null; then
        print_finding "Found IPs/WebSocket URLs in: $file"
        grep -n "185\.254\.136\.\|95\.141\.21\.\|ws://\|wss://" "$file" 2>/dev/null | head -10
        echo ""
    fi
done

# 11. List all Python scripts in common locations
print_section "11. All Python Scripts in User Directories"

echo "All Python scripts in /home..."
find /home -name "*.py" -type f 2>/dev/null | while read file; do
    echo "$file ($(stat -c%s "$file" 2>/dev/null || stat -f%z "$file" 2>/dev/null) bytes, modified: $(stat -c%y "$file" 2>/dev/null | cut -d' ' -f1 || stat -f%Sm "$file" 2>/dev/null))"
done
echo ""

# 12. Check systemd user services
print_section "12. Checking User Systemd Services"

for user_home in /home/*; do
    user=$(basename "$user_home")
    echo "Checking user: $user"
    if [ -d "$user_home/.config/systemd/user" ]; then
        ls -la "$user_home/.config/systemd/user/"
        cat "$user_home/.config/systemd/user/"*.service 2>/dev/null || true
    fi
    echo ""
done

# 13. Summary and recommendations
print_section "Summary and Next Steps"

echo -e "${YELLOW}=== Files to Review ===${NC}"
echo "Look through the output above for:"
echo "1. Python scripts mentioning 'sensor', 'lux', 'websocket', or 'yoctopuce'"
echo "2. Systemd services that might be running sensor code"
echo "3. Hardcoded IP addresses that need updating"
echo "4. Configuration files with WebSocket URLs"
echo "5. USB devices that might be the light sensor"
echo ""
echo -e "${YELLOW}=== Expected Pattern ===${NC}"
echo "The light sensor client likely:"
echo "- Reads from a Yoctopuce USB light sensor device"
echo "- Sends data via WebSocket to ws://IP:8765"
echo "- Runs as a systemd service or cron job"
echo "- May have hardcoded the old IP address (185.254.136.244)"
echo ""
echo -e "${GREEN}Diagnostic complete!${NC}"
echo ""
echo "To share this report:"
echo "  Save to file: /tmp/diagnose-lightsensor.sh > /tmp/lightsensor-report.txt"
echo "  View: cat /tmp/lightsensor-report.txt"
echo "  Copy back: scp user@media-server:/tmp/lightsensor-report.txt ."
