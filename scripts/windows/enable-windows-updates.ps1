<# =============================================================================
ENABLE WINDOWS UPDATES (maintenance mode) + optional install

Design goals:
- Safely reverses disable-windows-updates.ps1 changes
- Scoped: only removes what our lockdown script added (hosts markers, firewall group/rule)
- Clear output + explicit pause so output doesn’t vanish

Run: PowerShell as Administrator
============================================================================= #>

#Requires -RunAsAdministrator
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Script:GroupName = 'ScreenMachine Windows Update Control'
$Script:HostsMarkerStart = '# === SCREEN-MACHINE WINDOWS UPDATE BLOCK START ==='
$Script:HostsMarkerEnd   = '# === SCREEN-MACHINE WINDOWS UPDATE BLOCK END ==='
$Script:FirewallRuleName = 'ScreenMachine - Block Windows Update'

function Pause-Script([string]$Message = 'Press Enter to exit...') {
  try { [void](Read-Host $Message) } catch { }
}

function Write-Section([string]$Title) {
  Write-Host ''
  Write-Host $Title -ForegroundColor Cyan
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

Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  ENABLING WINDOWS UPDATES (MAINTENANCE)" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""

# -----------------------------------------------------------------------------
# 1. REMOVE HOSTS FILE BLOCKS
# -----------------------------------------------------------------------------
Write-Section "[1/6] Removing hosts file blocks (scoped markers)"

$hostsPath = "C:\Windows\System32\drivers\etc\hosts"
if (Test-Path $hostsPath) {
  $lines = Get-Content -Path $hostsPath -ErrorAction Stop
  $out = New-Object System.Collections.Generic.List[string]
  $inBlock = $false
  $removed = 0
  foreach ($line in $lines) {
    if ($line -eq $Script:HostsMarkerStart) { $inBlock = $true; $removed++; continue }
    if ($line -eq $Script:HostsMarkerEnd) { $inBlock = $false; $removed++; continue }
    if ($inBlock) { $removed++; continue }
    [void]$out.Add($line)
  }
  Set-Content -Path $hostsPath -Value $out -Encoding ASCII -Force
  Write-Host "  Removed lockdown hosts block (lines removed: $removed)" -ForegroundColor Green
} else {
  Write-Host "  Hosts file not found; skipping" -ForegroundColor Yellow
}

# -----------------------------------------------------------------------------
# 2. REMOVE REGISTRY POLICY BLOCKS
# -----------------------------------------------------------------------------
Write-Section "[2/6] Removing registry policy blocks (only keys/values we set)"

$policyPath = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate"
$auPath = "$policyPath\AU"

if (Test-Path $policyPath) {
  foreach ($name in @('DisableWindowsUpdateAccess','DoNotConnectToWindowsUpdateInternetLocations','SetDisableUXWUAccess')) {
    Remove-ItemProperty -Path $policyPath -Name $name -ErrorAction SilentlyContinue
  }
  Write-Host "  Cleared policy values under $policyPath" -ForegroundColor Green
}
if (Test-Path $auPath) {
  foreach ($name in @('NoAutoUpdate','AUOptions')) {
    Remove-ItemProperty -Path $auPath -Name $name -ErrorAction SilentlyContinue
  }
  Write-Host "  Cleared AU values under $auPath" -ForegroundColor Green
}

# Reset service start types in registry
$services = @("wuauserv", "UsoSvc", "WaaSMedicSvc", "BITS", "DoSvc")
foreach ($svc in $services) {
    $regPath = "HKLM:\SYSTEM\CurrentControlSet\Services\$svc"
    if (Test-Path $regPath) {
        # 2 = Automatic, 3 = Manual, 4 = Disabled
        $startType = switch ($svc) {
            "wuauserv" { 3 }      # Manual
            "UsoSvc" { 2 }        # Automatic
            "WaaSMedicSvc" { 3 }  # Manual
            "BITS" { 3 }          # Manual
            "DoSvc" { 2 }         # Automatic
        }
        Set-ItemProperty -Path $regPath -Name "Start" -Value $startType -Force
    }
}
Write-Host "  Registry policy blocks cleared" -ForegroundColor Green

# -----------------------------------------------------------------------------
# 3. REMOVE FIREWALL BLOCKS
# -----------------------------------------------------------------------------
Write-Section "[3/6] Removing firewall blocks (scoped rule)"

$rule = Get-NetFirewallRule -DisplayName $Script:FirewallRuleName -ErrorAction SilentlyContinue
if ($null -ne $rule) {
  Remove-NetFirewallRule -Name $rule.Name -ErrorAction SilentlyContinue
  Write-Host "  Removed firewall rule: $($Script:FirewallRuleName)" -ForegroundColor Green
} else {
  Write-Host "  No matching firewall rule found; skipping" -ForegroundColor Yellow
}

# -----------------------------------------------------------------------------
# 4. RE-ENABLE SERVICES
# -----------------------------------------------------------------------------
Write-Section "[4/6] Re-enabling Windows Update services"

$serviceConfig = @(
    @{Name="BITS"; StartType="Manual"},
    @{Name="DoSvc"; StartType="Automatic"},
    @{Name="wuauserv"; StartType="Manual"},
    @{Name="UsoSvc"; StartType="Automatic"},
    @{Name="WaaSMedicSvc"; StartType="Manual"}
)

foreach ($svc in $serviceConfig) {
  try {
    Set-Service -Name $svc.Name -StartupType $svc.StartType -ErrorAction SilentlyContinue
    # Try to start; some services may fail to start immediately, that's OK.
    Start-Service -Name $svc.Name -ErrorAction SilentlyContinue
    $status = (Get-Service -Name $svc.Name).Status
    Write-Host "  $($svc.Name): $status ($($svc.StartType))" -ForegroundColor Green
  } catch {
    Write-Host "  $($svc.Name): warning: $($_.Exception.Message)" -ForegroundColor Yellow
  }
}

# -----------------------------------------------------------------------------
# 5. RE-ENABLE SCHEDULED TASKS
# -----------------------------------------------------------------------------
Write-Section "[5/6] Re-enabling scheduled tasks (best-effort)"

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
    "\Microsoft\Windows\WaaSMedic\PerformRemediation"
)

$enabled = 0
foreach ($task in $tasks) {
  try {
    $parts = Split-TaskPathName -FullTaskPath $task
    Enable-ScheduledTask -TaskName $parts.TaskName -TaskPath $parts.TaskPath -ErrorAction Stop | Out-Null
    $enabled++
    Write-Host "  Enabled: $task" -ForegroundColor Green
  } catch {
    # Task may not exist / may already be enabled
  }
}
Write-Host "  Enabled $enabled scheduled tasks (where present)" -ForegroundColor Green

# -----------------------------------------------------------------------------
# 6. CHECK FOR UPDATES
# -----------------------------------------------------------------------------
Write-Section "[6/6] Checking for Windows Updates"
Write-Host ""

# Use Windows Update COM object
try {
    $UpdateSession = New-Object -ComObject Microsoft.Update.Session
    $UpdateSearcher = $UpdateSession.CreateUpdateSearcher()
    
    Write-Host "  Searching for updates (this may take a minute)..." -ForegroundColor Yellow
    $SearchResult = $UpdateSearcher.Search("IsInstalled=0 and IsHidden=0")
    
    if ($SearchResult.Updates.Count -eq 0) {
        Write-Host ""
        Write-Host "  No updates available." -ForegroundColor Green
    } else {
        Write-Host ""
        Write-Host "  Found $($SearchResult.Updates.Count) update(s):" -ForegroundColor Yellow
        Write-Host ""
        
        foreach ($Update in $SearchResult.Updates) {
          Write-Host "  - $($Update.Title)" -ForegroundColor White
        }
        
        Write-Host ""
        $confirm = Read-Host "  Do you want to download and install these updates? (Y/N)"
        
        if ($confirm -eq "Y" -or $confirm -eq "y") {
            Write-Host ""
            Write-Host "  Downloading updates..." -ForegroundColor Yellow
            
            $UpdatesToDownload = New-Object -ComObject Microsoft.Update.UpdateColl
            foreach ($Update in $SearchResult.Updates) {
              if ($Update.EulaAccepted -eq $false) { try { $Update.AcceptEula() } catch { } }
              $UpdatesToDownload.Add($Update) | Out-Null
            }
            
            $Downloader = $UpdateSession.CreateUpdateDownloader()
            $Downloader.Updates = $UpdatesToDownload
            $dlResult = $Downloader.Download()
            Write-Host "  Download result: $($dlResult.ResultCode)" -ForegroundColor Gray
            
            Write-Host "  Installing updates..." -ForegroundColor Yellow
            
            $Installer = $UpdateSession.CreateUpdateInstaller()
            $Installer.Updates = $UpdatesToDownload
            $InstallResult = $Installer.Install()
            
            Write-Host ""
            Write-Host "  Install result: $($InstallResult.ResultCode)" -ForegroundColor Gray
            if ($InstallResult.RebootRequired) {
                Write-Host "  Updates installed. REBOOT REQUIRED." -ForegroundColor Red
            } else {
                Write-Host "  Updates installed successfully." -ForegroundColor Green
            }
        } else {
            Write-Host ""
            Write-Host "  Update installation cancelled." -ForegroundColor Yellow
            Write-Host "  You can run Windows Update manually from Settings." -ForegroundColor Gray
        }
    }
} catch {
    Write-Host "  Could not use COM object. Opening Windows Update settings..." -ForegroundColor Yellow
    Start-Process "ms-settings:windowsupdate"
}

# -----------------------------------------------------------------------------
# DONE
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  WINDOWS UPDATES RE-ENABLED" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "To lock down updates again, run:" -ForegroundColor Yellow
Write-Host "  .\disable-windows-updates.ps1" -ForegroundColor White
Write-Host ""

Pause-Script




