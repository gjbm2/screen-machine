#!/bin/bash
# Kiosk System Deployment Script
# Usage: ./deploy-kiosk.sh [remote-host] [remote-user]

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

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

# Function to install dependencies
install_dependencies() {
    print_status "Installing system dependencies..."
    
    sudo apt update
    sudo apt upgrade -y
    
    sudo apt install -y \
        xorg \
        xserver-xorg-core \
        openbox \
        unclutter \
        wmctrl \
        xrandr \
        xset \
        snapd \
        wget \
        gnupg \
        curl
}

# Function to install Google Chrome
install_chrome() {
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
    
    # Check if script files exist in current directory
    local script_dir="$(dirname "$0")"
    local scripts=("kiosk-wait-start.sh" "kiosk-loop.sh" "webview-manager.sh")
    
    for script in "${scripts[@]}"; do
        if [[ ! -f "$script_dir/$script" ]]; then
            print_error "Script $script not found in $script_dir"
            print_status "Please ensure all script files are in the same directory as this deployment script"
            exit 1
        fi
    done
    
    # Copy scripts to system locations
    sudo cp "$script_dir/kiosk-wait-start.sh" /usr/local/bin/
    sudo cp "$script_dir/kiosk-loop.sh" /usr/local/bin/
    sudo cp "$script_dir/webview-manager.sh" /usr/local/bin/
    
    # Set correct ownership
    sudo chown root:root /usr/local/bin/kiosk-*.sh
    sudo chown gjbm2:gjbm2 /usr/local/bin/webview-manager.sh
    
    # Make executable
    sudo chmod +x /usr/local/bin/kiosk-*.sh
    sudo chmod +x /usr/local/bin/webview-manager.sh
    
    print_success "Scripts deployed successfully"
}

# Function to install systemd service
install_service() {
    print_status "Installing systemd service..."
    
    local script_dir="$(dirname "$0")"
    
    if [[ ! -f "$script_dir/kiosk.service" ]]; then
        print_error "Service file kiosk.service not found in $script_dir"
        exit 1
    fi
    
    # Copy service file
    sudo cp "$script_dir/kiosk.service" /etc/systemd/system/
    
    # Reload systemd
    sudo systemctl daemon-reload
    
    # Enable service
    sudo systemctl enable kiosk.service
    
    # Create log file
    sudo touch /var/log/kiosk.log
    sudo chown gjbm2:gjbm2 /var/log/kiosk.log
    
    print_success "Systemd service installed and enabled"
}

# Function to start service
start_service() {
    print_status "Starting kiosk service..."
    
    sudo systemctl start kiosk.service
    
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
    
    # Check service status
    if sudo systemctl is-active kiosk.service >/dev/null 2>&1; then
        print_success "✓ Kiosk service is running"
    else
        print_error "✗ Kiosk service is not running"
    fi
    
    # Check if Xorg is running
    if ps aux | grep -q "Xorg :0"; then
        print_success "✓ Xorg is running"
    else
        print_warning "✗ Xorg is not running"
    fi
    
    # Check if Chrome processes are running
    if ps aux | grep -q "google-chrome-stable.*kiosk"; then
        print_success "✓ Chrome kiosk processes are running"
    else
        print_warning "✗ Chrome kiosk processes not found"
    fi
    
    # Test display access
    if sudo -u gjbm2 DISPLAY=:0 xrandr --query >/dev/null 2>&1; then
        print_success "✓ Display access working"
    else
        print_warning "✗ Display access test failed"
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
    echo "  1. Install system dependencies"
    echo "  2. Install Google Chrome"
    echo "  3. Create user account (gjbm2)"
    echo "  4. Deploy kiosk scripts"
    echo "  5. Install systemd service"
    echo "  6. Start the kiosk service"
    echo "  7. Verify installation"
}

# Function for remote deployment
deploy_remote() {
    local remote_host=$1
    local remote_user=$2
    local script_dir="$(dirname "$0")"
    
    print_status "Deploying to remote host: $remote_user@$remote_host"
    
    # Check if SSH key is available
    if [[ ! -f ~/.ssh/id_rsa ]] && [[ ! -f ~/.ssh/id_ed25519 ]]; then
        print_warning "No SSH key found. You may need to enter password multiple times."
    fi
    
    # Validate files exist
    for file in kiosk-wait-start.sh kiosk-loop.sh webview-manager.sh kiosk.service; do
        if [[ ! -f "$script_dir/$file" ]]; then
            print_error "Required file not found: $file"
            exit 1
        fi
    done
    
    # Copy files to remote host
    print_status "Copying files to remote host..."
    scp "$script_dir/kiosk-wait-start.sh" \
        "$script_dir/kiosk-loop.sh" \
        "$script_dir/webview-manager.sh" \
        "$script_dir/kiosk.service" \
        $remote_user@$remote_host:/tmp/
    
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

# Update system
print_status "Updating system packages..."
sudo apt update && sudo apt upgrade -y

# Install dependencies
print_status "Installing system dependencies..."
sudo apt install -y xorg xserver-xorg-core openbox unclutter wmctrl xrandr xset snapd wget gnupg curl

# Install Chrome
print_status "Installing Google Chrome..."
wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | sudo apt-key add -
echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" | sudo tee /etc/apt/sources.list.d/google-chrome.list
sudo apt update
sudo apt install -y google-chrome-stable

# Create user
print_status "Setting up user account..."
sudo useradd -m -s /bin/bash gjbm2 2>/dev/null || print_warning "User gjbm2 already exists"
sudo usermod -aG audio,video gjbm2

# Copy scripts
print_status "Deploying scripts..."
sudo cp /tmp/kiosk-*.sh /usr/local/bin/
sudo cp /tmp/webview-manager.sh /usr/local/bin/
sudo chown root:root /usr/local/bin/kiosk-*.sh
sudo chown gjbm2:gjbm2 /usr/local/bin/webview-manager.sh
sudo chmod +x /usr/local/bin/kiosk-*.sh
sudo chmod +x /usr/local/bin/webview-manager.sh

# Install service
print_status "Installing systemd service..."
sudo cp /tmp/kiosk.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable kiosk.service

# Create log directory
print_status "Creating log directory..."
sudo touch /var/log/kiosk.log
sudo chown gjbm2:gjbm2 /var/log/kiosk.log

# Start service
print_status "Starting kiosk service..."
sudo systemctl start kiosk.service

# Wait for service to start
sleep 5

# Verify installation
print_status "Verifying installation..."
if sudo systemctl is-active kiosk.service >/dev/null 2>&1; then
    print_success "✓ Kiosk service is running"
else
    print_error "✗ Kiosk service failed to start"
    print_status "Check logs with: sudo journalctl -u kiosk.service"
fi

# Check if Xorg is running
if ps aux | grep -q "[X]org :0"; then
    print_success "✓ Xorg is running"
else
    print_warning "✗ Xorg is not running yet (may take a moment)"
fi

# Clean up temp files
print_status "Cleaning up temporary files..."
rm -f /tmp/kiosk-*.sh /tmp/webview-manager.sh /tmp/kiosk.service

print_success "Remote deployment complete!"
print_status "Monitor service with: sudo journalctl -u kiosk.service -f"
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
    
    # Execute deployment steps
    install_dependencies
    install_chrome
    create_user
    deploy_scripts
    install_service
    start_service
    verify_installation
    
    print_success "Deployment completed successfully!"
    echo ""
    print_status "Useful commands:"
    echo "  Check service status: sudo systemctl status kiosk.service"
    echo "  View service logs: sudo journalctl -u kiosk.service -f"
    echo "  Restart service: sudo systemctl restart kiosk.service"
    echo "  Check processes: ps aux | grep -E '(Xorg|chrome|kiosk)'"
}

# Run main function with all arguments
main "$@"
