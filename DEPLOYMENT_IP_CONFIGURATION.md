# Deployment IP Configuration Guide

## Overview
This document explains how IP addresses are configured in the Screen Machine application and how to update them when your ISP changes your public IP address.

## IP Configuration Architecture

### 1. Environment Variables (`.env` file)
The primary configuration is stored in `.env` file in the project root:

```bash
# --- Frontend (Vite) ---
VITE_API_URL=http://YOUR_PUBLIC_IP:5000/api   # Backend API URL
VITE_WS_HOST=ws://YOUR_PUBLIC_IP:8765         # WebSocket host

# --- Backend (Python) ---
VITE_URL=http://YOUR_PUBLIC_IP:8000           # Frontend URL
```

### 2. Server Ports
- **Flask API**: Port 5000 (configured in `config.py`)
- **Vite Dev Server**: Port 8080 (configured in `vite.config.ts`)
- **WebSocket Server**: Port 8765 (configured in `config.py`)

### 3. External Access
- **Direct API Access**: `http://YOUR_PUBLIC_IP:5000/api/`
- **Frontend Access**: `http://YOUR_PUBLIC_IP:8080/`
- **Ngrok Tunnel**: `http://YOUR_PUBLIC_IP:8000/` (if using ngrok)

## When Your ISP Changes Your IP Address

### Step 1: Update Environment Variables
```bash
# Navigate to project directory
cd /home/gjbm2/dev/screen-machine

# Update IP address in .env file
sed -i 's/OLD_IP_ADDRESS/NEW_IP_ADDRESS/g' .env

# Verify the changes
cat .env
```

### Step 2: Restart All Services
```bash
# Stop all services
bash ~/shutdown-webservers.sh

# Start Flask (Terminal 1)
cd /home/gjbm2/dev/screen-machine
source .venv/bin/activate
python3 app.py

# Start Vite (Terminal 2)
cd /home/gjbm2/dev/screen-machine
npm run dev

# Start Ngrok (Terminal 3)
ngrok http --domain=adapted-vervet-eternal.ngrok-free.app 8080
```

### Step 3: Update Media Server Scripts
```bash
# Find all shell scripts that might contain old IP
find /home/gjbm2 -name "*.sh" -exec grep -l "OLD_IP_ADDRESS" {} \;

# Update all found scripts
find /home/gjbm2 -name "*.sh" -exec sed -i 's/OLD_IP_ADDRESS/NEW_IP_ADDRESS/g' {} \;
```

## Configuration Files That Reference IP Addresses

### Primary Configuration
- **`.env`** - Main environment variables (PRIMARY SOURCE)
- **`config.py`** - Flask server configuration
- **`vite.config.ts`** - Vite development server configuration

### Code Files (Use Environment Variables)
- **`routes/publisher.py`** - Uses `VITE_API_URL` environment variable
- **`src/config.ts`** - Frontend configuration
- **`src/utils/api.ts`** - API client configuration

### Test Files
- **`tests/integration/test_url_formats.py`** - Test configuration

## Best Practices

### ✅ DO:
- Always use environment variables for IP addresses
- Update `.env` file as the single source of truth
- Use `localhost` as fallback defaults in code
- Document IP changes in this file

### ❌ DON'T:
- Hardcode IP addresses in source code
- Forget to update media server scripts
- Skip restarting services after IP changes

## Troubleshooting

### Services Not Accessible Externally
1. Check if services are running: `ps aux | grep -E "(flask|vite|ngrok)"`
2. Verify ports are open: `netstat -tlnp | grep -E "(5000|8080|8765)"`
3. Test local access: `curl -I http://localhost:5000/api/logs`
4. Check firewall settings on Windows/WSL

### Environment Variables Not Loading
1. Verify `.env` file exists and has correct format
2. Restart services after changing `.env`
3. Check for typos in variable names

### Media Server Issues
1. Find all scripts: `find /home/gjbm2 -name "*.sh"`
2. Search for old IP: `grep -r "OLD_IP" /home/gjbm2/`
3. Update all references: `sed -i 's/OLD_IP/NEW_IP/g' /path/to/script.sh`

## Quick Reference Commands

```bash
# Update IP in .env file
sed -i 's/185\.254\.136\.244/95.141.21.170/g' .env

# Find all shell scripts
find /home/gjbm2 -name "*.sh" -type f

# Search for old IP in all files
grep -r "185\.254\.136\.244" /home/gjbm2/

# Update all references to old IP
find /home/gjbm2 -type f -exec sed -i 's/185\.254\.136\.244/95.141.21.170/g' {} \;

# Restart all services
bash ~/shutdown-webservers.sh
```

## Last Updated
- **Date**: October 25, 2025
- **Old IP**: 185.254.136.244
- **New IP**: 95.141.21.170
- **Reason**: ISP changed public IP address



