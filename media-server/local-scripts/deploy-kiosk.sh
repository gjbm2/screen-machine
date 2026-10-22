#!/bin/bash
# Kiosk System Deployment Script
# Usage: ./deploy-kiosk.sh [remote-host] [remote-user]
#
# Installs the Wayland kiosk as the default (since 2026-10-13): kiosk.service runs sway, which starts
# kiosk-wayland.sh (one full-screen Chrome per TV). The X11 kiosk (Xorg + kiosk-loop.sh, one X screen per TV) is
# installed as kiosk-x11.service, DISABLED, as the fallback. Switch back on the box:
#   sudo systemctl disable --now kiosk && sudo systemctl enable --now kiosk-x11
# Files are read from media-server/remote-scripts/ (run from the repo, or from a copy of media-server/).

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Kiosk files, relative to SRC_DIR
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/remote-scripts"
KIOSK_FILES=(
    kiosk.service wayland/sway-kiosk.conf wayland/kiosk-wayland.sh            # default: Wayland (sway)
    kiosk-x11.service kiosk-wait-start.sh kiosk-loop.sh xorg/20-zaphod.conf    # fallback: X11, disabled
    webview-manager.sh
)

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to check if running as root
check_root() {
    if [[ $EUID -eq 0 ]]; then
        print_error "This script should not be run as root directly."
        print_status "Please run as a regular user with sudo privileges."
        exit 1
    fi
}

# Function to check if sudo is available
check_sudo() {
    if ! sudo -n true 2>/dev/null; then
        print_error "This script requires sudo privileges."
        print_status "Please ensure you can run sudo commands."
        exit 1
    fi
}

# Function to check that every kiosk file is present (before anything is installed)
check_files() {
    for file in "${KIOSK_FILES[@]}"; do
        if [[ ! -f "$SRC_DIR/$file" ]]; then
            print_error "Required file not found: $SRC_DIR/$file"
            print_status "Run this script from a copy of media-server/ (it reads media-server/remote-scripts/)"
            exit 1
        fi
    done
}

# Function to install dependencies
install_dependencies() {
    print_status "Installing system dependencies..."
    
    # No apt upgrade, and --no-upgrade on installs: a re-run on a live box must not upgrade the kernel, Chrome or
    # the display stack behind the back of lockdown_system's masked auto-updates
    sudo apt update
    
    # X packages are for the kiosk-x11.service fallback (x11-xserver-utils provides xrandr and xset);
    # jq is used by the status scripts (check-status*.sh, restart-kiosk.sh) and the admin API's display check
    sudo apt install -y --no-upgrade \
        xorg \
        xserver-xorg-core \
        openbox \
        unclutter \
        wmctrl \
        x11-xserver-utils \
        snapd \
        wget \
        gnupg \
        curl \
        jq
    
    # sway for the default (Wayland) kiosk.service; no recommends (skips xwayland, swayidle, foot, ...)
    sudo apt install -y --no-upgrade --no-install-recommends sway
}

# Function to install Google Chrome
install_chrome() {
    # an installed Chrome is left alone: the kiosk's video flags were tested on the box's Chrome (136.0.7103,
    # 2026-10-13), so upgrade it on purpose, not as a side effect of a re-run
    if dpkg-query -W -f='${Status}' google-chrome-stable 2>/dev/null | grep -q "ok installed"; then
        print_warning "Google Chrome already installed -- not upgrading"
        return
    fi
    
    print_status "Installing Google Chrome..."
    
    # Add Google Chrome repository
    wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | sudo apt-key add -
    echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" | sudo tee /etc/apt/sources.list.d/google-chrome.list
    
    sudo apt update
    sudo apt install -y google-chrome-stable
    
    # Verify installation
    if google-chrome-stable --version >/dev/null 2>&1; then
        print_success "Google Chrome installed successfully"
    else
        print_error "Google Chrome installation failed"
        exit 1
    fi
}

# Function to create user
create_user() {
    print_status "Setting up user account..."
    
    # Create user if it doesn't exist
    if ! id gjbm2 >/dev/null 2>&1; then
        sudo useradd -m -s /bin/bash gjbm2
        print_success "User gjbm2 created"
    else
        print_warning "User gjbm2 already exists"
    fi
    
    # Add to audio and video groups
    sudo usermod -aG audio,video gjbm2
    
    # Verify user creation
    if id gjbm2 >/dev/null 2>&1; then
        print_success "User gjbm2 configured successfully"
    else
        print_error "Failed to create user gjbm2"
        exit 1
    fi
}

# Function to deploy scripts
deploy_scripts() {
    print_status "Deploying kiosk scripts..."
    
    # Copy scripts to system locations (kiosk-wayland.sh: default; kiosk-wait-start.sh + kiosk-loop.sh: X11 fallback)
    sudo cp "$SRC_DIR/wayland/kiosk-wayland.sh" /usr/local/bin/
    sudo cp "$SRC_DIR/kiosk-wait-start.sh" /usr/local/bin/
    sudo cp "$SRC_DIR/kiosk-loop.sh" /usr/local/bin/
    sudo cp "$SRC_DIR/webview-manager.sh" /usr/local/bin/
    
    # Set correct ownership
    sudo chown root:root /usr/local/bin/kiosk-*.sh
    sudo chown gjbm2:gjbm2 /usr/local/bin/webview-manager.sh
    
    # Make executable
    sudo chmod 755 /usr/local/bin/kiosk-*.sh
    sudo chmod +x /usr/local/bin/webview-manager.sh
    
    # sway config for the Wayland kiosk (read by kiosk.service)
    sudo install -D -m 644 -o root -g root "$SRC_DIR/wayland/sway-kiosk.conf" /usr/local/share/kiosk/sway-kiosk.conf
    
    # One X screen per TV for the X11 fallback (only Xorg reads it, so it is inert while sway is the kiosk)
    sudo install -D -m 644 -o root -g root "$SRC_DIR/xorg/20-zaphod.conf" /etc/X11/xorg.conf.d/20-zaphod.conf
    
    # kiosk-wayland.sh options: created once, so a later KIOSK_VIDEO=copy survives a re-run
    if [[ ! -f /etc/default/kiosk-wayland ]]; then
        echo "KIOSK_VIDEO=zerocopy" | sudo tee /etc/default/kiosk-wayland >/dev/null
        print_success "Created /etc/default/kiosk-wayland (KIOSK_VIDEO=zerocopy)"
    else
        print_warning "/etc/default/kiosk-wayland already exists -- keeping it"
    fi
    
    print_success "Scripts deployed successfully"
}

# Function to lock down the system for kiosk use
lockdown_system() {
    print_status "Locking down system for kiosk use..."

    # Mask getty on tty1 -- prevents login prompt stealing VT1 from the kiosk (sway, or Xorg in the fallback)
    print_status "Masking getty@tty1.service (prevents VT1 conflict with the kiosk)..."
    sudo systemctl stop getty@tty1.service 2>/dev/null || true
    sudo systemctl disable getty@tty1.service 2>/dev/null || true
    sudo systemctl mask getty@tty1.service
    if systemctl is-enabled getty@tty1.service 2>/dev/null | grep -q masked; then
        print_success "getty@tty1.service masked"
    else
        print_warning "getty@tty1.service may not be fully masked -- verify manually"
    fi

    # Disable all automatic updates -- prevents surprise kernel upgrades and reboots
    print_status "Disabling automatic updates..."
    for svc in unattended-upgrades.service apt-daily.timer apt-daily-upgrade.timer apt-daily.service apt-daily-upgrade.service; do
        sudo systemctl stop "$svc" 2>/dev/null || true
        sudo systemctl disable "$svc" 2>/dev/null || true
        sudo systemctl mask "$svc" 2>/dev/null || true
    done
    if systemctl is-enabled unattended-upgrades.service 2>/dev/null | grep -q masked; then
        print_success "Automatic updates disabled and masked"
    else
        print_warning "unattended-upgrades may not be fully masked -- verify manually"
    fi

    print_success "System lockdown complete"
}

# Function to install systemd service
install_service() {
    print_status "Installing systemd services..."
    
    # Copy service files (kiosk = Wayland default, kiosk-x11 = X11 fallback)
    sudo cp "$SRC_DIR/kiosk.service" /etc/systemd/system/
    sudo cp "$SRC_DIR/kiosk-x11.service" /etc/systemd/system/
    
    # Reload systemd
    sudo systemctl daemon-reload
    
    # Enable the Wayland kiosk; the X11 fallback stays disabled (both units take tty1)
    sudo systemctl enable kiosk.service
    sudo systemctl disable kiosk-x11.service 2>/dev/null || true
    
    # Create log file (kiosk-wayland.sh and kiosk-loop.sh both log here as gjbm2)
    sudo touch /var/log/kiosk.log
    sudo chown gjbm2:gjbm2 /var/log/kiosk.log
    
    print_success "Systemd services installed (kiosk.service enabled, kiosk-x11.service disabled)"
}

# Function to start service
start_service() {
    print_status "Starting kiosk service..."
    
    # restart, not start: on a re-run (or a box whose kiosk.service was the X11 unit) the new files take effect now
    sudo systemctl restart kiosk.service
    
    # Wait a moment for service to start
    sleep 3
    
    # Check if service is running
    if sudo systemctl is-active kiosk.service >/dev/null 2>&1; then
        print_success "Kiosk service started successfully"
    else
        print_error "Failed to start kiosk service"
        print_status "Check service status with: sudo systemctl status kiosk.service"
        print_status "Check service logs with: sudo journalctl -u kiosk.service -f"
        exit 1
    fi
}

# Function to verify installation
verify_installation() {
    print_status "Verifying installation..."
    
    # Check system lockdown
    if systemctl is-enabled getty@tty1.service 2>/dev/null | grep -q masked; then
        print_success "✓ getty@tty1.service is masked"
    else
        print_error "✗ getty@tty1.service is NOT masked -- kiosk may fail after reboot!"
    fi
    
    if systemctl is-enabled unattended-upgrades.service 2>/dev/null | grep -q masked; then
        print_success "✓ Automatic updates are disabled"
    else
        print_error "✗ Automatic updates are NOT disabled -- system may update and break!"
    fi
    
    # Check service status
    if sudo systemctl is-active kiosk.service >/dev/null 2>&1; then
        print_success "✓ Kiosk service is running"
    else
        print_error "✗ Kiosk service is not running"
    fi
    
    if [[ "$(systemctl is-enabled kiosk-x11.service 2>/dev/null)" == "disabled" ]]; then
        print_success "✓ X11 fallback (kiosk-x11.service) installed and disabled"
    else
        print_warning "✗ kiosk-x11.service is not installed+disabled -- check: systemctl is-enabled kiosk-x11.service"
    fi
    
    # Check if sway is running
    if pgrep -x sway >/dev/null; then
        print_success "✓ sway is running"
    else
        print_warning "✗ sway is not running"
    fi
    
    # Check if Chrome processes are running (the real binary is /opt/google/chrome/chrome)
    if pgrep -f "chrome.*--kiosk" >/dev/null; then
        print_success "✓ Chrome kiosk processes are running"
    else
        print_warning "✗ Chrome kiosk processes not found (may take a moment)"
    fi
    
    # Check the TVs (DRM connector status: works under sway and Xorg, needs no display socket)
    local connected
    connected=$(cat /sys/class/drm/card*-HDMI-A-*/status 2>/dev/null | grep -cx connected || true)
    if [[ "$connected" -ge 2 ]]; then
        print_success "✓ Both HDMI outputs report a connected display"
    else
        print_warning "✗ ${connected:-0} HDMI output(s) report a connected display (expected 2)"
    fi
    
    print_status "Installation verification complete"
}

# Function to show usage
show_usage() {
    echo "Usage: $0 [remote-host] [remote-user]"
    echo ""
    echo "Local deployment:"
    echo "  $0"
    echo ""
    echo "Remote deployment:"
    echo "  $0 192.168.1.100 ubuntu"
    echo ""
    echo "This script will:"
    echo "  1. Install system dependencies (sway; X packages for the fallback)"
    echo "  2. Install Google Chrome (skipped if already installed; nothing installed is upgraded)"
    echo "  3. Create user account (gjbm2)"
    echo "  4. Lock down system (mask getty@tty1, disable auto-updates)"
    echo "  5. Deploy kiosk scripts and configs"
    echo "  6. Install systemd services (kiosk = Wayland/sway, enabled; kiosk-x11 = X11 fallback, disabled)"
    echo "  7. Start the kiosk service"
    echo "  8. Verify installation"
}

# Function for remote deployment
deploy_remote() {
    local remote_host=$1
    local remote_user=$2
    
    print_status "Deploying to remote host: $remote_user@$remote_host"
    
    # Check if SSH key is available
    if [[ ! -f ~/.ssh/id_rsa ]] && [[ ! -f ~/.ssh/id_ed25519 ]]; then
        print_warning "No SSH key found. You may need to enter password multiple times."
    fi
    
    # Validate files exist
    check_files
    
    # Copy files to remote host (flat into /tmp; every basename is unique)
    print_status "Copying files to remote host..."
    local sources=()
    for file in "${KIOSK_FILES[@]}"; do sources+=("$SRC_DIR/$file"); done
    scp "${sources[@]}" $remote_user@$remote_host:/tmp/
    
    # Execute deployment on remote host
    print_status "Executing deployment on remote host..."
    ssh $remote_user@$remote_host << 'EOF'
set -e

# Colors for remote output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_status() { echo -e "${BLUE}[INFO]${NC} $1"; }
print_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# apt/dpkg get </dev/null and no prompts: this script arrives on stdin, so a prompt would read the rest of it
export DEBIAN_FRONTEND=noninteractive

# Update package lists. No upgrade, and --no-upgrade on installs: a re-run on a live box must not upgrade the
# kernel, Chrome or the display stack behind the back of the masked auto-updates
print_status "Updating package lists..."
sudo -E apt-get update </dev/null

# Install dependencies (X packages for the kiosk-x11 fallback; sway for the default kiosk, no recommends;
# jq for the status scripts and the admin API's display check)
print_status "Installing system dependencies..."
sudo -E apt-get install -y --no-upgrade xorg xserver-xorg-core openbox unclutter wmctrl x11-xserver-utils snapd wget gnupg curl jq </dev/null
sudo -E apt-get install -y --no-upgrade --no-install-recommends sway </dev/null

# Install Chrome, unless installed: the kiosk's video flags were tested on the box's Chrome (136.0.7103, 2026-10-13),
# so upgrade it on purpose, not as a side effect of a re-run
if dpkg-query -W -f='${Status}' google-chrome-stable 2>/dev/null | grep -q "ok installed"; then
    print_warning "Google Chrome already installed -- not upgrading"
else
    print_status "Installing Google Chrome..."
    wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | sudo apt-key add -
    echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" | sudo tee /etc/apt/sources.list.d/google-chrome.list
    sudo -E apt-get update </dev/null
    sudo -E apt-get install -y google-chrome-stable </dev/null
fi

# Create user
print_status "Setting up user account..."
sudo useradd -m -s /bin/bash gjbm2 2>/dev/null || print_warning "User gjbm2 already exists"
sudo usermod -aG audio,video gjbm2

# Lock down system for kiosk use
print_status "Locking down system for kiosk use..."

# Mask getty on tty1 (prevents VT1 conflict with the kiosk: sway, or Xorg in the fallback)
sudo systemctl stop getty@tty1.service 2>/dev/null || true
sudo systemctl disable getty@tty1.service 2>/dev/null || true
sudo systemctl mask getty@tty1.service
print_success "getty@tty1.service masked"

# Disable all automatic updates (prevents surprise kernel upgrades/reboots)
for svc in unattended-upgrades.service apt-daily.timer apt-daily-upgrade.timer apt-daily.service apt-daily-upgrade.service; do
    sudo systemctl stop "$svc" 2>/dev/null || true
    sudo systemctl disable "$svc" 2>/dev/null || true
    sudo systemctl mask "$svc" 2>/dev/null || true
done
print_success "Automatic updates disabled and masked"

# Copy scripts (kiosk-wayland.sh: default; kiosk-wait-start.sh + kiosk-loop.sh: X11 fallback)
print_status "Deploying scripts..."
sudo cp /tmp/kiosk-wayland.sh /tmp/kiosk-wait-start.sh /tmp/kiosk-loop.sh /usr/local/bin/
sudo cp /tmp/webview-manager.sh /usr/local/bin/
sudo chown root:root /usr/local/bin/kiosk-*.sh
sudo chown gjbm2:gjbm2 /usr/local/bin/webview-manager.sh
sudo chmod 755 /usr/local/bin/kiosk-*.sh
sudo chmod +x /usr/local/bin/webview-manager.sh

# sway config (default kiosk) and one X screen per TV (X11 fallback; only Xorg reads it)
sudo install -D -m 644 -o root -g root /tmp/sway-kiosk.conf /usr/local/share/kiosk/sway-kiosk.conf
sudo install -D -m 644 -o root -g root /tmp/20-zaphod.conf /etc/X11/xorg.conf.d/20-zaphod.conf

# kiosk-wayland.sh options: created once, so a later KIOSK_VIDEO=copy survives a re-run
if [[ ! -f /etc/default/kiosk-wayland ]]; then
    echo "KIOSK_VIDEO=zerocopy" | sudo tee /etc/default/kiosk-wayland >/dev/null
    print_success "Created /etc/default/kiosk-wayland (KIOSK_VIDEO=zerocopy)"
else
    print_warning "/etc/default/kiosk-wayland already exists -- keeping it"
fi

# Install services: kiosk (Wayland) enabled, kiosk-x11 (fallback) disabled -- both take tty1
print_status "Installing systemd services..."
sudo cp /tmp/kiosk.service /tmp/kiosk-x11.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable kiosk.service
sudo systemctl disable kiosk-x11.service 2>/dev/null || true

# Create log directory
print_status "Creating log directory..."
sudo touch /var/log/kiosk.log
sudo chown gjbm2:gjbm2 /var/log/kiosk.log

# Start service (restart, so a re-run picks up the new files)
print_status "Starting kiosk service..."
sudo systemctl restart kiosk.service

# Wait for service to start
sleep 5

# Verify installation
print_status "Verifying installation..."

# Check lockdown
if systemctl is-enabled getty@tty1.service 2>/dev/null | grep -q masked; then
    print_success "✓ getty@tty1.service is masked"
else
    print_error "✗ getty@tty1.service is NOT masked"
fi

if systemctl is-enabled unattended-upgrades.service 2>/dev/null | grep -q masked; then
    print_success "✓ Automatic updates are disabled"
else
    print_error "✗ Automatic updates are NOT disabled"
fi

if sudo systemctl is-active kiosk.service >/dev/null 2>&1; then
    print_success "✓ Kiosk service is running"
else
    print_error "✗ Kiosk service failed to start"
    print_status "Check logs with: sudo journalctl -u kiosk.service"
fi

if [[ "$(systemctl is-enabled kiosk-x11.service 2>/dev/null)" == "disabled" ]]; then
    print_success "✓ X11 fallback (kiosk-x11.service) installed and disabled"
else
    print_warning "✗ kiosk-x11.service is not installed+disabled"
fi

# Check if sway is running
if pgrep -x sway >/dev/null; then
    print_success "✓ sway is running"
else
    print_warning "✗ sway is not running yet (may take a moment)"
fi

# Clean up temp files
print_status "Cleaning up temporary files..."
rm -f /tmp/kiosk-wayland.sh /tmp/kiosk-wait-start.sh /tmp/kiosk-loop.sh /tmp/webview-manager.sh \
      /tmp/kiosk.service /tmp/kiosk-x11.service /tmp/sway-kiosk.conf /tmp/20-zaphod.conf

print_success "Remote deployment complete!"
print_status "Monitor service with: sudo journalctl -u kiosk.service -f"
print_status "X11 fallback: sudo systemctl disable --now kiosk && sudo systemctl enable --now kiosk-x11"
EOF
}

# Main execution
main() {
    echo "=========================================="
    echo "    Kiosk System Deployment Script"
    echo "=========================================="
    echo ""
    
    # Check if remote deployment
    if [[ $# -eq 2 ]]; then
        deploy_remote "$1" "$2"
        exit 0
    elif [[ $# -eq 0 ]]; then
        # Local deployment
        print_status "Starting local deployment..."
    else
        show_usage
        exit 1
    fi
    
    # Check prerequisites
    check_root
    check_sudo
    check_files
    
    # Execute deployment steps
    install_dependencies
    install_chrome
    create_user
    lockdown_system
    deploy_scripts
    install_service
    start_service
    verify_installation
    
    print_success "Deployment completed successfully!"
    echo ""
    print_status "Useful commands:"
    echo "  Check service status: sudo systemctl status kiosk.service"
    echo "  View service logs: sudo journalctl -u kiosk.service -f   (launcher log: /var/log/kiosk.log)"
    echo "  Restart service: sudo systemctl restart kiosk.service"
    echo "  Check processes: ps aux | grep -E '(sway|Xorg|chrome|kiosk)'"
    echo "  Switch to X11 fallback: sudo systemctl disable --now kiosk && sudo systemctl enable --now kiosk-x11"
    echo "  Video path: KIOSK_VIDEO=zerocopy|copy in /etc/default/kiosk-wayland (restart kiosk.service after a change)"
    print_status "Not done here: VA-API hardware decode (experiments/stream-latency/beelink/enable-hwdecode.sh)"
}

# Run main function with all arguments
main "$@"
