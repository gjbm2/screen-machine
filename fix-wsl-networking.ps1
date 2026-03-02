# =============================================================================
# FIX WSL NETWORKING AFTER WINDOWS UPDATE/REBOOT
# Run this script as Administrator
# =============================================================================

<# =============================================================================
FIX WSL NETWORKING (portproxy + firewall) for Screen Machine

This script restores the Windows-host side plumbing after a reboot/update:
- Ensures IP Helper service is running (required for portproxy)
- Rebuilds portproxy rules to the current WSL IP
- Optionally opens inbound firewall ports for the host
- Verifies each port with Test-NetConnection

Matches your router forwarding setup:
- WAN:8000 -> Windows:8080 (Vite)
- WAN:5000 -> Windows:5000 (Flask)
- WAN:8765 -> Windows:8765 (Overlay WS)
- WAN:2222 -> Windows:2222 (SSH -> WSL:22)
============================================================================= #>

#Requires -RunAsAdministrator
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Script:FirewallGroup = 'ScreenMachine WSL Portproxy'

function Pause-Script([string]$Message = 'Press Enter to exit...') {
  try { [void](Read-Host $Message) } catch { }
}

function Write-Section([string]$Title) {
  Write-Host ''
  Write-Host $Title -ForegroundColor Cyan
}

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  FIXING WSL NETWORK ACCESS (Screen Machine)" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# -----------------------------------------------------------------------------
# 1. GET CURRENT WSL IP
# -----------------------------------------------------------------------------
Write-Host "[1/5] Getting WSL IP address..." -ForegroundColor Cyan

$wslIp = (wsl hostname -I).Trim().Split()[0]
if (-not $wslIp) {
    Write-Host "ERROR: Could not get WSL IP. Is WSL running?" -ForegroundColor Red
    Pause-Script
    exit 1
}
Write-Host "  WSL IP: $wslIp" -ForegroundColor Green

# -----------------------------------------------------------------------------
# 2. ENSURE IP HELPER SERVICE RUNNING (required for portproxy)
# -----------------------------------------------------------------------------
Write-Section "[2/5] Ensuring IP Helper service is running (iphlpsvc)"
try {
  Set-Service -Name iphlpsvc -StartupType Automatic -ErrorAction SilentlyContinue
  Start-Service -Name iphlpsvc -ErrorAction SilentlyContinue
  $svc = Get-Service -Name iphlpsvc -ErrorAction Stop
  Write-Host "  iphlpsvc: $($svc.Status) ($($svc.StartType))" -ForegroundColor Green
} catch {
  Write-Host "  WARNING: Could not start IP Helper (portproxy may not work): $($_.Exception.Message)" -ForegroundColor Yellow
}

# -----------------------------------------------------------------------------
# 3. (OPTIONAL) SET NETWORK TO PRIVATE (allows LAN inbound)
# -----------------------------------------------------------------------------
Write-Section "[3/5] Network profile (informational)"
try {
  Get-NetConnectionProfile | Format-Table Name, NetworkCategory, InterfaceAlias -AutoSize
  Write-Host "  If you need LAN inbound access, ensure the profile is Private." -ForegroundColor Gray
} catch {
  # non-fatal
}

# -----------------------------------------------------------------------------
# 4. SET UP PORT FORWARDING (portproxy)
# -----------------------------------------------------------------------------
Write-Section "[4/5] Rebuilding portproxy rules to current WSL IP"

# Router forwards WAN:8000 -> Windows:8080, so portproxy must listen on 8080 (not 8000)
$mappings = @(
  @{ ListenPort = 8080; ConnectPort = 8080; Name = 'Vite' },
  @{ ListenPort = 5000; ConnectPort = 5000; Name = 'Flask' },
  @{ ListenPort = 8765; ConnectPort = 8765; Name = 'Overlay WS' },
  @{ ListenPort = 2222; ConnectPort = 22;   Name = 'SSH' }
)

foreach ($m in $mappings) {
  $lp = [int]$m.ListenPort
  $cp = [int]$m.ConnectPort
  netsh interface portproxy delete v4tov4 listenport=$lp listenaddress=0.0.0.0 2>$null | Out-Null
  netsh interface portproxy add v4tov4 listenport=$lp listenaddress=0.0.0.0 connectport=$cp connectaddress=$wslIp | Out-Null
  Write-Host ("  {0,-9} 0.0.0.0:{1} -> {2}:{3}" -f $m.Name, $lp, $wslIp, $cp) -ForegroundColor Green
}

# Add/ensure inbound firewall rules for the LISTEN ports (host side)
Write-Section "[5/5] Ensuring inbound firewall rules for host listen ports"
foreach ($m in $mappings) {
  $lp = [int]$m.ListenPort
  $name = "ScreenMachine - Allow inbound TCP $lp"
  $existing = Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue
  if ($null -eq $existing) {
    New-NetFirewallRule -DisplayName $name -Group $Script:FirewallGroup -Direction Inbound -Action Allow -Protocol TCP -LocalPort $lp -Profile Any | Out-Null
    Write-Host "  Created firewall rule: $name" -ForegroundColor Green
  } else {
    Set-NetFirewallRule -DisplayName $name -Enabled True | Out-Null
    Write-Host "  Firewall rule ok: $name" -ForegroundColor Yellow
  }
}

# Show current rules
Write-Host ""
Write-Host "Current portproxy rules:" -ForegroundColor Cyan
netsh interface portproxy show v4tov4

Write-Host ""
Write-Host "Verification (host side):" -ForegroundColor Cyan
foreach ($m in $mappings) {
  $lp = [int]$m.ListenPort
  try {
    $tnc = Test-NetConnection -ComputerName 127.0.0.1 -Port $lp -WarningAction SilentlyContinue
    $ok = $tnc.TcpTestSucceeded
    $color = if ($ok) { 'Green' } else { 'Yellow' }
    Write-Host ("  {0,-9} localhost:{1} => {2}" -f $m.Name, $lp, ($ok ? 'OK' : 'FAIL')) -ForegroundColor $color
  } catch {
    Write-Host ("  {0,-9} localhost:{1} => ERROR: {2}" -f $m.Name, $lp, $_.Exception.Message) -ForegroundColor Yellow
  }
}

Write-Host ""
Write-Host "Notes:" -ForegroundColor Cyan
Write-Host "  - Router: WAN:8000 -> Windows:8080 -> WSL:8080 (via portproxy)" -ForegroundColor Gray
Write-Host "  - Public URL should be reachable at: http://95.141.21.170:8000/" -ForegroundColor Gray
Write-Host ""
Pause-Script




