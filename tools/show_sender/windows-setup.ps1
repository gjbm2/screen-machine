# windows-setup.ps1: let show_service (in WSL) hear this Windows machine's default microphone, now and at every logon.
# For a deployment whose show.yaml lists a sender on host "windows" (deployments/README.md, "Room audio on a WSL
# machine"). PC22 does this through Spectre's audio-agent card instead; this script is for machines without Spectre.
#
# Run once, in an ADMIN PowerShell (the firewall needs it):
#   powershell -ExecutionPolicy Bypass -File \\wsl.localhost\Ubuntu\home\reg\dev\screen-machine\tools\show_sender\windows-setup.ps1
# First copy the agent from WSL: bash tools/show_sender/deploy-pc22.sh
#
# What it does:
#   1. Firewall: removes Block rules for this Python's pythonw.exe (Windows makes them when its "Allow access" prompt is
#      dismissed; a Block beats any Allow), and allows inbound UDP on the agent's port from WSL's address range only.
#   2. Logon: a hidden launcher in your Startup folder runs the agent at every logon (in your session, where Windows
#      lets desktop apps use the mic).
#   3. Starts the agent now if it isn't running.
param(
    [int]$Port = 6984,
    [string]$Stream = "room-mic",
    [string]$Python = "$env:LOCALAPPDATA\Programs\Python\Python312\pythonw.exe",
    [string]$AgentDir = "$env:LOCALAPPDATA\screen-machine\audio-agent",
    [string]$FromAddresses = "172.16.0.0/12",    # WSL's NAT range
    [switch]$Raw                                 # the mic without Windows' echo cancelling / noise suppression
                                                 # (pc22_agent.py --raw; the karaoke laptop, 25 Nov)
)
$ErrorActionPreference = "Stop"
if (-not (Test-Path $Python)) { throw "No pythonw at $Python (install Python 3.12, then: py -3.12 -m pip install --user sounddevice numpy)" }
if (-not (Test-Path "$AgentDir\pc22_agent.py")) { throw "No agent in ${AgentDir}; run tools/show_sender/deploy-pc22.sh in WSL first" }

# 1. Firewall
$blocks = Get-NetFirewallRule -Direction Inbound -Action Block -ErrorAction SilentlyContinue |
    Where-Object { ($_ | Get-NetFirewallApplicationFilter).Program -ieq $Python }
foreach ($r in $blocks) { Write-Host "removing Block rule '$($r.DisplayName)' ($($r.Name))"; Remove-NetFirewallRule -Name $r.Name }
$name = "screen-machine-audio-agent-udp-$Port"
Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -Name $name -DisplayName "screen-machine audio agent (UDP $Port from WSL)" -Direction Inbound `
    -Protocol UDP -LocalPort $Port -RemoteAddress $FromAddresses -Program $Python -Action Allow -Profile Any | Out-Null
Write-Host "firewall: allowed UDP $Port from $FromAddresses to $Python"

# 2. Start at logon
$startup = [Environment]::GetFolderPath("Startup")
$vbs = Join-Path $startup "screen-machine-audio-agent.vbs"
$q = '""'
$rawArg = if ($Raw) { " --raw" } else { "" }
$cmd = "$q$Python$q $q$AgentDir\pc22_agent.py$q --port $Port --stream $Stream$rawArg --log $q$AgentDir\agent.log$q"
Set-Content -Path $vbs -Encoding ASCII -Value @(
    "' screen-machine audio agent: this machine's default mic to show_service (stream $Stream, udp $Port).",
    "' Made by tools/show_sender/windows-setup.ps1. Log: $AgentDir\agent.log",
    "CreateObject(""WScript.Shell"").Run ""$cmd"", 0, False"
)
Write-Host "logon: $vbs"

# 3. Running now?
$running = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" | Where-Object { $_.CommandLine -match 'pc22_agent\.py' }
if ($running) { Write-Host "agent: already running (pid $($running.ProcessId -join ', '))" }
else { & wscript.exe $vbs; Write-Host "agent: started" }
Write-Host "done. In WSL: tail the agent log; within a few seconds it should say 'mic: off (show_service: nothing needs it)' (heard, mic idle) or 'on (subscribed)'."
