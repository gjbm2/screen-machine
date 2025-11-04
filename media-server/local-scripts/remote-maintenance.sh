#!/bin/bash
# Remote Maintenance Harness
# General-purpose script to deploy scripts to media server, execute them, and collect results
#
# Usage:
#   ./remote-maintenance.sh <command> [options]
#
# Commands:
#   diagnose-lightsensor   Run light sensor diagnostic
#   update-ip <old> <new>  Update IP addresses in kiosk scripts
#   run-script <path>      Deploy and run a custom script
#   collect-logs           Collect all relevant logs
#   system-status          Get system status report
#   fix-lightsensor <ip>   Deploy light sensor fix with new IP
#
# Examples:
#   ./remote-maintenance.sh diagnose-lightsensor
#   ./remote-maintenance.sh update-ip 185.254.136.244 95.141.21.170
#   ./remote-maintenance.sh run-script my-custom-check.sh
#   ./remote-maintenance.sh collect-logs
#   ./remote-maintenance.sh system-status

set -e

# Get the parent directory (media-server)
MEDIA_SERVER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="$MEDIA_SERVER_DIR/maintenance.log"

# Redirect ALL output to log file AND console
exec > >(tee -a "$LOG_FILE")
exec 2>&1

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load configuration from .env file
if [ -f "$SCRIPT_DIR/.env" ]; then
    # Export variables from .env
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
    echo "✓ Loaded configuration from .env"
elif [ -f "$SCRIPT_DIR/.env.example" ]; then
    echo "⚠️  Warning: .env file not found!"
    echo "   Create it by copying .env.example:"
    echo "   cp $SCRIPT_DIR/.env.example $SCRIPT_DIR/.env"
    echo ""
    echo "   Then edit .env with your credentials"
    echo "   See SETUP_CREDENTIALS.md for detailed instructions"
    echo ""
    read -p "Continue with defaults? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Configuration with fallbacks
REMOTE_HOST="${MEDIA_SERVER_HOST:-screen-machine-drawingroom}"
REMOTE_USER="${MEDIA_SERVER_USER:-gjbm2}"
REMOTE_PORT="${MEDIA_SERVER_PORT:-22}"
SSH_KEY="${MEDIA_SERVER_SSH_KEY:-}"
SSH_PASSWORD="${MEDIA_SERVER_PASSWORD:-}"
REMOTE_TMP="${REMOTE_TMP_DIR:-/tmp/maintenance}-$$"
LOCAL_RESULTS="${MAINTENANCE_RESULTS_DIR:-./maintenance-results}"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)

# Build SSH and SCP commands with options
SSH_OPTS="-p $REMOTE_PORT"
SCP_OPTS="-P $REMOTE_PORT"  # SCP uses uppercase -P for port

if [ ! -z "$SSH_KEY" ]; then
    SSH_OPTS="$SSH_OPTS -i $SSH_KEY"
    SCP_OPTS="$SCP_OPTS -i $SSH_KEY"
fi

SSH_CMD="ssh $SSH_OPTS"
SCP_CMD="scp $SCP_OPTS"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Functions
print_header() {
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo ""
}

print_status() {
    echo -e "${GREEN}[✓]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[!]${NC} $1"
}

print_error() {
    echo -e "${RED}[✗]${NC} $1"
}

# Ensure results directory exists
mkdir -p "$LOCAL_RESULTS"

# Check SSH connectivity
check_connection() {
    print_status "Testing SSH connection to $REMOTE_USER@$REMOTE_HOST:$REMOTE_PORT..."
    
    # Check if using SSH key and if it exists
    if [ ! -z "$SSH_KEY" ] && [ ! -f "$SSH_KEY" ]; then
        print_error "SSH key not found: $SSH_KEY"
        return 1
    fi
    
    # Use sshpass if password is set
    local test_cmd
    if [ ! -z "$SSH_PASSWORD" ] && command -v sshpass &> /dev/null; then
        test_cmd="sshpass -p '$SSH_PASSWORD' $SSH_CMD"
    else
        test_cmd="$SSH_CMD"
    fi
    
    if $test_cmd -o ConnectTimeout=5 "$REMOTE_USER@$REMOTE_HOST" "echo 'Connection successful'" &>/dev/null; then
        print_status "Connection verified"
        return 0
    else
        print_error "Cannot connect to $REMOTE_USER@$REMOTE_HOST:$REMOTE_PORT"
        print_warning "Check your .env file configuration:"
        echo "  MEDIA_SERVER_HOST=$REMOTE_HOST"
        echo "  MEDIA_SERVER_USER=$REMOTE_USER"
        echo "  MEDIA_SERVER_PORT=$REMOTE_PORT"
        if [ ! -z "$SSH_KEY" ]; then
            echo "  MEDIA_SERVER_SSH_KEY=$SSH_KEY"
        fi
        echo ""
        print_warning "Or test SSH manually:"
        echo "  ssh -p $REMOTE_PORT $REMOTE_USER@$REMOTE_HOST"
        return 1
    fi
}

# Deploy a script to remote server
deploy_script() {
    local script_path="$1"
    local remote_path="$2"
    
    print_status "Deploying $script_path to $remote_path..."
    $SCP_CMD -q "$script_path" "$REMOTE_USER@$REMOTE_HOST:$remote_path"
    $SSH_CMD "$REMOTE_USER@$REMOTE_HOST" "chmod +x $remote_path"
    print_status "Deployed successfully"
}

# Execute command on remote server and collect output
execute_remote() {
    local command="$1"
    local output_file="$2"
    
    print_status "Executing remote command..."
    
    # Run command - output automatically goes to log via exec redirect
    $SSH_CMD "$REMOTE_USER@$REMOTE_HOST" "$command" 2>&1 | tee "$output_file"
    
    print_status "Output saved to: $output_file"
}

# Collect a file from remote server
collect_file() {
    local remote_path="$1"
    local local_path="$2"
    
    print_status "Collecting $remote_path..."
    $SCP_CMD -q "$REMOTE_USER@$REMOTE_HOST:$remote_path" "$local_path" 2>/dev/null || {
        print_warning "File not found: $remote_path"
        return 1
    }
    print_status "Collected to: $local_path"
}

# Cleanup remote temporary files
cleanup_remote() {
    print_status "Cleaning up remote temporary files..."
    $SSH_CMD "$REMOTE_USER@$REMOTE_HOST" "rm -rf $REMOTE_TMP" 2>/dev/null || true
}

# Command: diagnose-lightsensor
cmd_diagnose_lightsensor() {
    echo "========================================="
    echo "Starting light sensor diagnostic for $REMOTE_USER@$REMOTE_HOST"
    echo "========================================="
    
    print_header "Light Sensor Diagnostic"
    
    local script="diagnose-lightsensor.sh"
    local output="$LOCAL_RESULTS/lightsensor-diagnostic-$TIMESTAMP.txt"
    
    # Check in current directory and parent directory
    if [ -f "$script" ]; then
        script="./$script"
    elif [ -f "../$script" ]; then
        script="../$script"
    else
        print_error "Script not found: $script"
        print_warning "Make sure diagnose-lightsensor.sh is in the local-scripts directory"
        echo "ERROR: diagnose-lightsensor.sh not found"
        exit 1
    fi
    
    check_connection || exit 1
    
    # Create remote temp directory
    $SSH_CMD "$REMOTE_USER@$REMOTE_HOST" "mkdir -p $REMOTE_TMP"
    
    # Deploy and execute (extract just the filename)
    local script_name=$(basename "$script")
    deploy_script "$script" "$REMOTE_TMP/$script_name"
    execute_remote "$REMOTE_TMP/$script_name" "$output"
    
    # Cleanup
    cleanup_remote
    
    print_header "Diagnostic Complete"
    print_status "Results saved to: $output"
    echo "========================================="
    echo "Light sensor diagnostic completed - output saved to $output"
    echo "========================================="
    echo ""
    print_status "Review the output to identify:"
    echo "  - Light sensor client script location"
    echo "  - Systemd service name"
    echo "  - Hardcoded IP addresses"
    echo "  - USB sensor device"
    echo ""
    echo "View results: cat $output"
    echo "View log: cat $LOG_FILE"
}

# Command: update-ip
cmd_update_ip() {
    local old_ip="$1"
    local new_ip="$2"
    
    if [ -z "$old_ip" ] || [ -z "$new_ip" ]; then
        print_error "Usage: $0 update-ip <old_ip> <new_ip>"
        exit 1
    fi
    
    echo "========================================="
    echo "Starting IP update: $old_ip → $new_ip"
    echo "========================================="
    
    print_header "Update IP Address: $old_ip → $new_ip"
    
    check_connection || exit 1
    
    local output="$LOCAL_RESULTS/ip-update-$TIMESTAMP.txt"
    
    # Create update script
    local update_script="$REMOTE_TMP/update-ip.sh"
    $SSH_CMD "$REMOTE_USER@$REMOTE_HOST" "mkdir -p $REMOTE_TMP"
    
    print_status "Creating update script..."
    $SSH_CMD "$REMOTE_USER@$REMOTE_HOST" "cat > $update_script" <<EOF
#!/bin/bash
set -e
echo "=== Updating IP addresses from $old_ip to $new_ip ==="
echo ""

# Escape dots for sed
OLD_ESC=\$(echo "$old_ip" | sed 's/\./\\./g')
NEW_IP="$new_ip"

echo "Files to update:"
echo "  - /usr/local/bin/kiosk-loop.sh"
echo "  - /usr/local/bin/webview-manager.sh"
echo ""

# Backup first
echo "Creating backups..."
sudo cp /usr/local/bin/kiosk-loop.sh /usr/local/bin/kiosk-loop.sh.bak-$TIMESTAMP
sudo cp /usr/local/bin/webview-manager.sh /usr/local/bin/webview-manager.sh.bak-$TIMESTAMP

# Update IPs
echo "Updating kiosk-loop.sh..."
sudo sed -i "s/\$OLD_ESC/\$NEW_IP/g" /usr/local/bin/kiosk-loop.sh

echo "Updating webview-manager.sh..."
sudo sed -i "s/\$OLD_ESC/\$NEW_IP/g" /usr/local/bin/webview-manager.sh

# Verify changes
echo ""
echo "=== Verification ==="
echo "kiosk-loop.sh URLs:"
grep -n "URL_" /usr/local/bin/kiosk-loop.sh || true

echo ""
echo "webview-manager.sh URLs:"
grep -n "URL" /usr/local/bin/webview-manager.sh || true

echo ""
echo "=== Backups created ==="
ls -lah /usr/local/bin/*.bak-$TIMESTAMP

echo ""
echo "=== Restarting kiosk service ==="
sudo systemctl restart kiosk.service
sleep 3
sudo systemctl status kiosk.service --no-pager

echo ""
echo "✓ Update complete!"
EOF
    
    $SSH_CMD "$REMOTE_USER@$REMOTE_HOST" "chmod +x $update_script"
    
    # Execute and collect output
    execute_remote "$update_script" "$output"
    
    # Cleanup
    cleanup_remote
    
    print_header "IP Update Complete"
    print_status "Results saved to: $output"
    echo "========================================="
    echo "IP update completed - output saved to $output"
    echo "========================================="
}

# Command: run-script
cmd_run_script() {
    local script_path="$1"
    
    if [ -z "$script_path" ] || [ ! -f "$script_path" ]; then
        print_error "Usage: $0 run-script <script_path>"
        print_error "Script not found: $script_path"
        exit 1
    fi
    
    local script_name=$(basename "$script_path")
    
    echo "========================================="
    echo "Running custom script: $script_name"
    echo "========================================="
    
    print_header "Running Custom Script: $script_name"
    
    check_connection || exit 1
    
    local output="$LOCAL_RESULTS/${script_name%.*}-$TIMESTAMP.txt"
    
    # Create remote temp directory
    $SSH_CMD "$REMOTE_USER@$REMOTE_HOST" "mkdir -p $REMOTE_TMP"
    
    # Deploy and execute
    deploy_script "$script_path" "$REMOTE_TMP/$script_name"
    execute_remote "$REMOTE_TMP/$script_name" "$output"
    
    # Cleanup
    cleanup_remote
    
    print_header "Script Execution Complete"
    print_status "Results saved to: $output"
    echo "========================================="
    echo "Custom script execution completed - output saved to $output"
    echo "========================================="
}

# Command: collect-logs
cmd_collect_logs() {
    echo "========================================="
    echo "Starting log collection from $REMOTE_USER@$REMOTE_HOST"
    echo "========================================="
    
    print_header "Collecting System Logs"
    
    check_connection || exit 1
    
    local log_dir="$LOCAL_RESULTS/logs-$TIMESTAMP"
    mkdir -p "$log_dir"
    
    print_status "Collecting kiosk service logs..."
    $SSH_CMD "$REMOTE_USER@$REMOTE_HOST" "sudo journalctl -u kiosk.service --since '24 hours ago' --no-pager" > "$log_dir/kiosk-service.log" 2>&1 || true
    
    print_status "Collecting kiosk.log..."
    collect_file "/var/log/kiosk.log" "$log_dir/kiosk.log" || true
    
    print_status "Collecting system logs..."
    $SSH_CMD "$REMOTE_USER@$REMOTE_HOST" "sudo journalctl --since '24 hours ago' --no-pager | tail -1000" > "$log_dir/system.log" 2>&1 || true
    
    print_status "Collecting process list..."
    $SSH_CMD "$REMOTE_USER@$REMOTE_HOST" "ps aux | grep -E '(chrome|kiosk|sensor|python)' | grep -v grep" > "$log_dir/processes.txt" 2>&1 || true
    
    print_status "Collecting network connections..."
    $SSH_CMD "$REMOTE_USER@$REMOTE_HOST" "sudo lsof -i -n -P" > "$log_dir/network-connections.txt" 2>&1 || true
    
    print_header "Log Collection Complete"
    print_status "Logs saved to: $log_dir"
    
    echo "========================================="
    echo "Log collection completed - files saved to $log_dir"
    echo "Files collected:"
    ls -lh "$log_dir"
    echo "========================================="
    
    echo ""
    echo "Files collected:"
    ls -lh "$log_dir"
}

# Command: system-status
cmd_system_status() {
    echo "========================================="
    echo "Starting system status report for $REMOTE_USER@$REMOTE_HOST"
    echo "========================================="
    
    print_header "System Status Report"
    
    check_connection || exit 1
    
    local output="$LOCAL_RESULTS/system-status-$TIMESTAMP.txt"
    
    print_status "Generating system status report..."
    
    $SSH_CMD "$REMOTE_USER@$REMOTE_HOST" "cat > $REMOTE_TMP/status.sh" <<'EOF'
#!/bin/bash
echo "=== System Status Report ==="
echo "Date: $(date)"
echo "Hostname: $(hostname)"
echo ""

echo "=== Kiosk Service ==="
sudo systemctl status kiosk.service --no-pager || true
echo ""

echo "=== Running Processes ==="
ps aux | grep -E "(Xorg|chrome|kiosk|sensor|python)" | grep -v grep || echo "No relevant processes found"
echo ""

echo "=== Display Status ==="
sudo -u gjbm2 DISPLAY=:0 xrandr --query 2>&1 || echo "Cannot query display"
echo ""

echo "=== Network Connections ==="
sudo lsof -i :5000 -i :8000 -i :8765 -n -P 2>&1 || echo "No connections on ports 5000, 8000, 8765"
echo ""

echo "=== USB Devices ==="
lsusb 2>&1 || echo "lsusb not available"
echo ""

echo "=== Disk Usage ==="
df -h / /home
echo ""

echo "=== Memory Usage ==="
free -h
echo ""

echo "=== Uptime ==="
uptime
echo ""

echo "=== Recent Errors (last 50 lines) ==="
sudo journalctl -p err --since "24 hours ago" --no-pager | tail -50 || true
EOF
    
    $SSH_CMD "$REMOTE_USER@$REMOTE_HOST" "chmod +x $REMOTE_TMP/status.sh"
    execute_remote "$REMOTE_TMP/status.sh" "$output"
    
    cleanup_remote
    
    print_header "Status Report Complete"
    print_status "Report saved to: $output"
    echo "========================================="
    echo "System status report completed - output saved to $output"
    echo "========================================="
    echo ""
    echo "View report: cat $output"
}

# Command: fix-lightsensor
cmd_fix_lightsensor() {
    local new_ip="$1"
    
    if [ -z "$new_ip" ]; then
        print_error "Usage: $0 fix-lightsensor <new_ip>"
        exit 1
    fi
    
    echo "========================================="
    echo "Starting light sensor fix with IP: $new_ip"
    echo "========================================="
    
    print_header "Fix Light Sensor with IP: $new_ip"
    
    check_connection || exit 1
    
    local output="$LOCAL_RESULTS/lightsensor-fix-$TIMESTAMP.txt"
    
    print_status "This will:"
    echo "  1. Find all Python scripts with websocket/sensor code"
    echo "  2. Update any hardcoded IP addresses to $new_ip"
    echo "  3. Restart related services"
    echo ""
    
    read -p "Continue? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_warning "Aborted by user"
        exit 0
    fi
    
    $SSH_CMD "$REMOTE_USER@$REMOTE_HOST" "mkdir -p $REMOTE_TMP"
    
    $SSH_CMD "$REMOTE_USER@$REMOTE_HOST" "cat > $REMOTE_TMP/fix-sensor.sh" <<EOF
#!/bin/bash
set -e
NEW_IP="$new_ip"

echo "=== Light Sensor Fix Script ==="
echo "Target IP: \$NEW_IP"
echo ""

echo "=== Searching for sensor scripts ==="
find /home /usr/local /opt -name "*.py" -type f 2>/dev/null | while read file; do
    if grep -l -i "sensor\|lux\|websocket.*8765\|yoctopuce" "\$file" 2>/dev/null; then
        echo "Found: \$file"
        
        # Check for old IPs
        if grep -l "185\.254\.136\.\|ws://" "\$file" 2>/dev/null; then
            echo "  → Contains old IP address or ws:// URL"
            echo "  → Backing up to \${file}.bak-$TIMESTAMP"
            cp "\$file" "\${file}.bak-$TIMESTAMP"
            
            # Update IPs
            sed -i 's/185\.254\.136\.[0-9]\+/'\$NEW_IP'/g' "\$file"
            sed -i 's|ws://[^:]*:|ws://'\$NEW_IP':|g' "\$file"
            
            echo "  → Updated!"
            grep -n "ws://\|websocket" "\$file" | head -5
        fi
        echo ""
    fi
done

echo "=== Searching for sensor services ==="
systemctl list-unit-files --type=service 2>/dev/null | grep -i "sensor\|lux\|light" || echo "No sensor services found"

echo ""
echo "=== Checking for running sensor processes ==="
ps aux | grep -i "sensor\|lux\|yoctopuce" | grep -v grep || echo "No sensor processes found"

echo ""
echo "✓ Fix complete!"
echo ""
echo "Next steps:"
echo "1. Review the changes above"
echo "2. Restart any sensor services manually if found"
echo "3. Check if sensor data is flowing: curl http://\$NEW_IP:5000/api/lightsensor/lightsense"
EOF
    
    $SSH_CMD "$REMOTE_USER@$REMOTE_HOST" "chmod +x $REMOTE_TMP/fix-sensor.sh"
    execute_remote "$REMOTE_TMP/fix-sensor.sh" "$output"
    
    cleanup_remote
    
    print_header "Light Sensor Fix Complete"
    print_status "Results saved to: $output"
    echo "========================================="
    echo "Light sensor fix completed - output saved to $output"
    echo "========================================="
    echo ""
    print_warning "Review the output and manually restart any sensor services found"
}

# Deploy updated kiosk scripts
cmd_deploy_scripts() {
    local script_name="${1:-all}"
    local auto_restart="${2:-}"
    
    print_header "Deploy Kiosk Scripts"
    
    # Determine which scripts to deploy
    local scripts_to_deploy
    if [ "$script_name" == "all" ]; then
        scripts_to_deploy=("kiosk-loop.sh" "kiosk-wait-start.sh" "webview-manager.sh")
        print_status "Deploying ALL kiosk scripts..."
    else
        scripts_to_deploy=("$script_name")
        print_status "Deploying: $script_name"
    fi
    
    echo ""
    
    # Deploy each script
    local deployed_count=0
    for script in "${scripts_to_deploy[@]}"; do
        local local_path="$MEDIA_SERVER_DIR/remote-scripts/$script"
        
        if [ ! -f "$local_path" ]; then
            print_error "Local script not found: $local_path"
            continue
        fi
        
        print_status "Deploying $script..."
        
        # Copy to temp location
        if [ ! -z "$SSH_PASSWORD" ] && command -v sshpass &> /dev/null; then
            sshpass -p "$SSH_PASSWORD" $SCP_CMD "$local_path" "$REMOTE_USER@$REMOTE_HOST:/tmp/$script"
        else
            $SCP_CMD "$local_path" "$REMOTE_USER@$REMOTE_HOST:/tmp/$script"
        fi
        
        # Move to final location and set permissions
        if [ ! -z "$SSH_PASSWORD" ] && command -v sshpass &> /dev/null; then
            sshpass -p "$SSH_PASSWORD" $SSH_CMD "$REMOTE_USER@$REMOTE_HOST" << EOF
echo "$SSH_PASSWORD" | sudo -S mv /tmp/$script /usr/local/bin/$script
echo "$SSH_PASSWORD" | sudo -S chmod 755 /usr/local/bin/$script
echo "$SSH_PASSWORD" | sudo -S chown root:root /usr/local/bin/$script
EOF
        else
            $SSH_CMD "$REMOTE_USER@$REMOTE_HOST" << EOF
sudo mv /tmp/$script /usr/local/bin/$script
sudo chmod 755 /usr/local/bin/$script
sudo chown root:root /usr/local/bin/$script
EOF
        fi
        
        print_status "✓ Deployed: $script"
        ((deployed_count++))
        echo ""
    done
    
    if [ $deployed_count -eq 0 ]; then
        print_error "No scripts were deployed!"
        return 1
    fi
    
    # Restart kiosk service
    echo ""
    print_warning "Scripts deployed successfully!"
    echo ""
    
    # Check if auto-restart flag is set
    if [[ "$auto_restart" == "--restart" ]] || [[ "$auto_restart" == "-r" ]]; then
        print_status "Auto-restarting kiosk service..."
        print_status "Restarting kiosk service..."
        
        if [ ! -z "$SSH_PASSWORD" ] && command -v sshpass &> /dev/null; then
            sshpass -p "$SSH_PASSWORD" $SSH_CMD "$REMOTE_USER@$REMOTE_HOST" << EOF
echo "=== Stopping kiosk service ==="
echo "$SSH_PASSWORD" | sudo -S systemctl stop kiosk.service
sleep 2
echo "✓ Stopped"
echo ""

echo "=== Starting kiosk service ==="
echo "$SSH_PASSWORD" | sudo -S systemctl start kiosk.service
sleep 3
echo "✓ Started"
echo ""

echo "=== Service Status ==="
systemctl status kiosk.service --no-pager | head -15
EOF
        else
            $SSH_CMD "$REMOTE_USER@$REMOTE_HOST" << EOF
echo "=== Stopping kiosk service ==="
sudo systemctl stop kiosk.service
sleep 2
echo "✓ Stopped"
echo ""

echo "=== Starting kiosk service ==="
sudo systemctl start kiosk.service
sleep 3
echo "✓ Started"
echo ""

echo "=== Service Status ==="
systemctl status kiosk.service --no-pager | head -15
EOF
        fi
        
        echo ""
        print_status "Kiosk service restarted"
        echo ""
        echo "Chrome console logs are now available at:"
        echo "  - /tmp/chrome-north/chrome_console.log"
        echo "  - /tmp/chrome-south/chrome_console.log"
    else
        echo "To apply changes, you need to restart the kiosk service."
        echo "This will briefly interrupt the displays."
        echo ""
        echo "Run: ./restart-kiosk.sh"
        echo "Or:  ./remote-maintenance.sh deploy-scripts --restart"
    fi
    
    echo ""
    print_header "Deployment Complete"
}

# Main command dispatcher
case "${1:-}" in
    diagnose-lightsensor)
        cmd_diagnose_lightsensor
        ;;
    update-ip)
        cmd_update_ip "$2" "$3"
        ;;
    run-script)
        cmd_run_script "$2"
        ;;
    collect-logs)
        cmd_collect_logs
        ;;
    system-status)
        cmd_system_status
        ;;
    fix-lightsensor)
        cmd_fix_lightsensor "$2"
        ;;
    deploy-scripts)
        cmd_deploy_scripts "$2" "$3"
        ;;
    *)
        print_header "Remote Maintenance Harness"
        echo "Usage: $0 <command> [options]"
        echo ""
        echo "Commands:"
        echo "  diagnose-lightsensor          Run light sensor diagnostic"
        echo "  update-ip <old> <new>         Update IP addresses in kiosk scripts"
        echo "  deploy-scripts [script] [-r]  Deploy kiosk scripts (use -r/--restart to auto-restart)"
        echo "  run-script <path>             Deploy and run a custom script"
        echo "  collect-logs                  Collect all relevant logs"
        echo "  system-status                 Get system status report"
        echo "  fix-lightsensor <new_ip>      Find and fix light sensor IP configuration"
        echo ""
        echo "Environment Variables:"
        echo "  MEDIA_SERVER_HOST             Remote hostname (default: screen-machine-drawingroom)"
        echo "  MEDIA_SERVER_USER             Remote username (default: gjbm2)"
        echo ""
        echo "Examples:"
        echo "  $0 diagnose-lightsensor"
        echo "  $0 update-ip 185.254.136.244 95.141.21.170"
        echo "  $0 deploy-scripts --restart          # Deploy all and auto-restart"
        echo "  $0 deploy-scripts kiosk-loop.sh -r  # Deploy specific and auto-restart"
        echo "  $0 system-status"
        echo "  $0 fix-lightsensor 95.141.21.170"
        echo "  $0 run-script my-custom-check.sh"
        echo ""
        echo "Results are saved to: $LOCAL_RESULTS/"
        exit 1
        ;;
esac

