<# =============================================================================
DISABLE ALL WINDOWS 11 UPDATES (lockdown mode)

Design goals:
- Idempotent: safe to run repeatedly (daily via scheduler)
- Reversible: paired with enable-windows-updates.ps1
- Scoped: only touches entries it creates (hosts markers, firewall group)
- Scheduled mode: runs silently with logging when -Scheduled flag passed
- Clear output + explicit pause in interactive mode

Run manually:   powershell -ExecutionPolicy Bypass -File disable-windows-updates.ps1
Run scheduled:  powershell -ExecutionPolicy Bypass -File disable-windows-updates.ps1 -Scheduled

============================================================================= #>

param(
  [switch]$Scheduled  # When set, runs silently with logging (no pause, no color output to console)
)

#Requires -RunAsAdministrator
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# -----------------------------------------------------------------------------
# CONFIGURATION
# -----------------------------------------------------------------------------
$Script:GroupName = 'ScreenMachine Windows Update Control'
$Script:HostsMarkerStart = '# === SCREEN-MACHINE WINDOWS UPDATE BLOCK START ==='
$Script:HostsMarkerEnd   = '# === SCREEN-MACHINE WINDOWS UPDATE BLOCK END ==='
$Script:FirewallRuleName = 'ScreenMachine - Block Windows Update'
$Script:LogFile = "$env:USERPROFILE\Desktop\windows-update-lockdown.log"

# -----------------------------------------------------------------------------
# LOGGING / OUTPUT HELPERS
# -----------------------------------------------------------------------------
function Write-Log {
  param([string]$Message, [string]$Level = 'INFO')
  $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
  $logLine = "[$timestamp] [$Level] $Message"
  
  if ($Scheduled) {
    # Append to log file only
    Add-Content -Path $Script:LogFile -Value $logLine -ErrorAction SilentlyContinue
  } else {
    # Write to console with color
    $color = switch ($Level) {
      'INFO'    { 'White' }
      'SUCCESS' { 'Green' }
      'WARN'    { 'Yellow' }
      'ERROR'   { 'Red' }
      'HEADER'  { 'Cyan' }
      default   { 'White' }
    }
    Write-Host $logLine -ForegroundColor $color
    # Also log to file for audit trail
    Add-Content -Path $Script:LogFile -Value $logLine -ErrorAction SilentlyContinue
  }
}

function Write-Section([string]$Title) {
  Write-Log $Title -Level 'HEADER'
}

function Pause-Script([string]$Message = 'Press Enter to exit...') {
  if (-not $Scheduled) {
    try { [void](Read-Host $Message) } catch { }
  }
}

function Split-TaskPathName([Parameter(Mandatory)][string]$FullTaskPath) {
  $t = $FullTaskPath.Trim()
  if (-not $t.StartsWith('\')) { throw "Task path must start with '\': $FullTaskPath" }
  $last = $t.LastIndexOf('\')
  if ($last -lt 1 -or $last -ge ($t.Length - 1)) { throw "Invalid task path: $FullTaskPath" }
  @{
    TaskPath = $t.Substring(0, $last + 1)
    TaskName = $t.Substring($last + 1)
  }
}

# -----------------------------------------------------------------------------
# BANNER
# -----------------------------------------------------------------------------
Write-Log "============================================" -Level 'HEADER'
Write-Log "  WINDOWS UPDATE LOCKDOWN - DAILY ENFORCEMENT" -Level 'HEADER'
Write-Log "============================================" -Level 'HEADER'
Write-Log "Mode: $(if ($Scheduled) { 'SCHEDULED (silent)' } else { 'INTERACTIVE' })" -Level 'INFO'

# -----------------------------------------------------------------------------
# 1. STOP AND DISABLE WINDOWS UPDATE SERVICES
# -----------------------------------------------------------------------------
Write-Section "[1/6] Disabling Windows Update services"

$services = @(
    "wuauserv",           # Windows Update
    "UsoSvc",             # Update Orchestrator Service
    "WaaSMedicSvc",       # Windows Update Medic Service
    "BITS",               # Background Intelligent Transfer Service
    "DoSvc"               # Delivery Optimization
)

foreach ($svc in $services) {
  try {
    Stop-Service -Name $svc -Force -ErrorAction SilentlyContinue
    Set-Service -Name $svc -StartupType Disabled -ErrorAction SilentlyContinue

    # Registry value is the authoritative source for startup type:
    # 2=Automatic, 3=Manual, 4=Disabled
    $regPath = "HKLM:\SYSTEM\CurrentControlSet\Services\$svc"
    if (Test-Path $regPath) {
      Set-ItemProperty -Path $regPath -Name "Start" -Value 4 -Force
    }

    $svcObj = Get-Service -Name $svc -ErrorAction SilentlyContinue
    if ($null -ne $svcObj) {
      Write-Log ("  {0,-12} => {1,-8} / {2}" -f $svc, $svcObj.Status, $svcObj.StartType) -Level 'SUCCESS'
    } else {
      Write-Log "  $svc => disabled" -Level 'SUCCESS'
    }
  } catch {
    Write-Log "  $svc => warning: $($_.Exception.Message)" -Level 'WARN'
  }
}

# -----------------------------------------------------------------------------
# 2. BLOCK UPDATE SERVERS VIA HOSTS FILE
# -----------------------------------------------------------------------------
Write-Section "[2/6] Blocking Microsoft update servers in hosts file"

$hostsPath = "C:\Windows\System32\drivers\etc\hosts"
$blockList = @(
  "0.0.0.0 update.microsoft.com",
  "0.0.0.0 windowsupdate.microsoft.com",
  "0.0.0.0 download.windowsupdate.com",
  "0.0.0.0 download.microsoft.com",
  "0.0.0.0 wustat.windows.com",
  "0.0.0.0 ntservicepack.microsoft.com",
  "0.0.0.0 fe2.update.microsoft.com",
  "0.0.0.0 fe3.delivery.mp.microsoft.com",
  "0.0.0.0 sls.update.microsoft.com",
  "0.0.0.0 ctldl.windowsupdate.com",
  "0.0.0.0 v10.vortex-win.data.microsoft.com",
  "0.0.0.0 settings-win.data.microsoft.com"
)

# Remove any existing block between our markers, then append a fresh block.
$lines = @()
if (Test-Path $hostsPath) {
  $lines = Get-Content -Path $hostsPath -ErrorAction Stop
}

$out = New-Object System.Collections.Generic.List[string]
$inBlock = $false
foreach ($line in $lines) {
  if ($line -eq $Script:HostsMarkerStart) { $inBlock = $true; continue }
  if ($line -eq $Script:HostsMarkerEnd) { $inBlock = $false; continue }
  if (-not $inBlock) { [void]$out.Add($line) }
}

# Ensure newline separation from last line if needed
if ($out.Count -gt 0 -and $out[$out.Count - 1] -ne '') { [void]$out.Add('') }

[void]$out.Add($Script:HostsMarkerStart)
foreach ($entry in $blockList) { [void]$out.Add($entry) }
[void]$out.Add($Script:HostsMarkerEnd)

Set-Content -Path $hostsPath -Value $out -Encoding ASCII -Force
Write-Log ("  Hosts file updated with {0} blocked domains" -f $blockList.Count) -Level 'SUCCESS'

# -----------------------------------------------------------------------------
# 3. REGISTRY KEYS TO DISABLE UPDATES (Policy-based)
# -----------------------------------------------------------------------------
Write-Section "[3/6] Setting registry policies to disable updates"

# Create policy paths if they don't exist
$policyPath = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate"
$auPath = "$policyPath\AU"

if (-not (Test-Path $policyPath)) {
    New-Item -Path $policyPath -Force | Out-Null
}
if (-not (Test-Path $auPath)) {
    New-Item -Path $auPath -Force | Out-Null
}

# Set policy values - these are the most reliable on Pro/Workstation editions
$regSettings = @(
    @{Path=$policyPath; Name="DisableWindowsUpdateAccess"; Value=1},
    @{Path=$policyPath; Name="DoNotConnectToWindowsUpdateInternetLocations"; Value=1},
    @{Path=$policyPath; Name="SetDisableUXWUAccess"; Value=1},
    @{Path=$policyPath; Name="ExcludeWUDriversInQualityUpdate"; Value=1},
    # Target release version - pin to current version to prevent feature upgrades
    @{Path=$policyPath; Name="TargetReleaseVersion"; Value=1},
    @{Path=$policyPath; Name="TargetReleaseVersionInfo"; Value="24H2"},
    # AU policies
    @{Path=$auPath; Name="NoAutoUpdate"; Value=1},
    @{Path=$auPath; Name="AUOptions"; Value=1},
    @{Path=$auPath; Name="NoAutoRebootWithLoggedOnUsers"; Value=1}
)

foreach ($reg in $regSettings) {
  $type = if ($reg.Value -is [string]) { 'String' } else { 'DWord' }
  Set-ItemProperty -Path $reg.Path -Name $reg.Name -Value $reg.Value -Type $type -Force
  Write-Log "  Set: $($reg.Name) = $($reg.Value)" -Level 'SUCCESS'
}

# -----------------------------------------------------------------------------
# 4. DISABLE WINDOWS UPDATE SCHEDULED TASKS
# -----------------------------------------------------------------------------
Write-Section "[4/6] Disabling Windows Update scheduled tasks"

$tasks = @(
    "\Microsoft\Windows\WindowsUpdate\Scheduled Start",
    "\Microsoft\Windows\WindowsUpdate\sih",
    "\Microsoft\Windows\WindowsUpdate\sihboot",
    "\Microsoft\Windows\UpdateOrchestrator\Schedule Scan",
    "\Microsoft\Windows\UpdateOrchestrator\Schedule Scan Static Task",
    "\Microsoft\Windows\UpdateOrchestrator\USO_UxBroker",
    "\Microsoft\Windows\UpdateOrchestrator\Reboot",
    "\Microsoft\Windows\UpdateOrchestrator\Reboot_AC",
    "\Microsoft\Windows\UpdateOrchestrator\Reboot_Battery",
    "\Microsoft\Windows\UpdateOrchestrator\Report policies",
    "\Microsoft\Windows\UpdateOrchestrator\UpdateModelTask",
    "\Microsoft\Windows\UpdateOrchestrator\PerformRemediation",
    "\Microsoft\Windows\WaaSMedic\PerformRemediation"
)

$disabled = 0
foreach ($task in $tasks) {
  try {
    $parts = Split-TaskPathName -FullTaskPath $task
    Disable-ScheduledTask -TaskName $parts.TaskName -TaskPath $parts.TaskPath -ErrorAction Stop | Out-Null
    $disabled++
  } catch {
    # Task may not exist / may already be disabled
  }
}
Write-Log "  Disabled $disabled scheduled tasks (best-effort)" -Level 'SUCCESS'

# -----------------------------------------------------------------------------
# 5. CREATE FIREWALL RULE TO BLOCK UPDATE TRAFFIC
# -----------------------------------------------------------------------------
Write-Section "[5/6] Managing firewall rules to block update traffic"

$fwRuleName = $Script:FirewallRuleName
$existingRule = Get-NetFirewallRule -DisplayName $fwRuleName -ErrorAction SilentlyContinue

if (-not $existingRule) {
  New-NetFirewallRule -DisplayName $fwRuleName `
    -Group $Script:GroupName `
    -Description "Blocks Windows Update service from accessing the internet" `
    -Direction Outbound `
    -Action Block `
    -Program "C:\Windows\System32\svchost.exe" `
    -Service wuauserv `
    -Enabled True | Out-Null
  Write-Log "  Created firewall rule: $fwRuleName" -Level 'SUCCESS'
} else {
  Set-NetFirewallRule -DisplayName $fwRuleName -Enabled True | Out-Null
  Write-Log "  Firewall rule already exists and is enabled" -Level 'INFO'
}

# Additional firewall rules for other update processes
$additionalRules = @(
  @{Name="ScreenMachine - Block UsoClient"; Program="C:\Windows\System32\UsoClient.exe"},
  @{Name="ScreenMachine - Block MoUsoCoreWorker"; Program="C:\Windows\System32\MoUsoCoreWorker.exe"},
  @{Name="ScreenMachine - Block WaaSMedic"; Program="C:\Windows\System32\WaaSMedicAgent.exe"}
)

foreach ($rule in $additionalRules) {
  $existing = Get-NetFirewallRule -DisplayName $rule.Name -ErrorAction SilentlyContinue
  if (-not $existing) {
    if (Test-Path $rule.Program) {
      New-NetFirewallRule -DisplayName $rule.Name `
        -Group $Script:GroupName `
        -Direction Outbound `
        -Action Block `
        -Program $rule.Program `
        -Enabled True | Out-Null
      Write-Log "  Created: $($rule.Name)" -Level 'SUCCESS'
    }
  } else {
    Set-NetFirewallRule -DisplayName $rule.Name -Enabled True | Out-Null
  }
}

# -----------------------------------------------------------------------------
# 6. VERIFY LOCKDOWN
# -----------------------------------------------------------------------------
Write-Section "[6/6] Verifying lockdown state"

$issues = 0

# Check services
foreach ($svc in $services) {
  $svcObj = Get-Service -Name $svc -ErrorAction SilentlyContinue
  if ($null -eq $svcObj) { continue }
  if ($svcObj.Status -ne 'Stopped') {
    Write-Log "  DRIFT: $svc is $($svcObj.Status) (expected Stopped)" -Level 'WARN'
    $issues++
  }
  if ($svcObj.StartType -ne 'Disabled') {
    Write-Log "  DRIFT: $svc StartType is $($svcObj.StartType) (expected Disabled)" -Level 'WARN'
    $issues++
  }
}

# Check registry
$regCheck = Get-ItemProperty -Path $policyPath -Name "DisableWindowsUpdateAccess" -ErrorAction SilentlyContinue
if ($regCheck.DisableWindowsUpdateAccess -ne 1) {
    Write-Log "  DRIFT: DisableWindowsUpdateAccess policy not set" -Level 'WARN'
    $issues++
}

if ($issues -eq 0) {
  Write-Log "  All checks passed - lockdown is active" -Level 'SUCCESS'
} else {
  Write-Log "  Found $issues issue(s) - some protections may have been reset by Windows" -Level 'WARN'
}

# -----------------------------------------------------------------------------
# SUMMARY
# -----------------------------------------------------------------------------
Write-Log "============================================" -Level 'HEADER'
Write-Log "  LOCKDOWN ENFORCEMENT COMPLETE" -Level 'HEADER'
Write-Log "============================================" -Level 'HEADER'
Write-Log "Applied protections:" -Level 'INFO'
Write-Log "  [x] Services stopped + disabled (registry-level)" -Level 'INFO'
Write-Log "  [x] Hosts file block (12 domains)" -Level 'INFO'
Write-Log "  [x] Windows Update policies set (including version pin to 24H2)" -Level 'INFO'
Write-Log "  [x] Scheduled tasks disabled" -Level 'INFO'
Write-Log "  [x] Firewall outbound block rules" -Level 'INFO'
Write-Log "" -Level 'INFO'
Write-Log "Log file: $Script:LogFile" -Level 'INFO'
Write-Log "To re-enable updates: .\enable-windows-updates.ps1" -Level 'INFO'

Pause-Script
