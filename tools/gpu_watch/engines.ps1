# The GPU's use by each Windows process and engine, one JSON line every 2 s (read by utils/gpu_watch.py from WSL).
# {"t": <unix s>, "u": {"<process>|<engine>": <percent>}}. Everything WSL runs (CUDA, NVENC) is the process vmwp.
# It reads the whole "GPU Engine" counter set at once (Get-Counter's wildcard takes ~10 s a read; this ~20 ms) and
# exits as soon as its reader goes away.
$ErrorActionPreference = "SilentlyContinue"
$cat = New-Object System.Diagnostics.PerformanceCounterCategory("GPU Engine")
$names = @{}
$prev = $null
while ($true) {
  $d = $cat.ReadCategory()["Utilization Percentage"]
  $cur = @{}
  if ($d) { foreach ($k in $d.Keys) { $cur[$k] = $d[$k].Sample } }
  if ($prev) {
    $agg = @{}
    foreach ($k in $cur.Keys) {
      if (-not $prev.ContainsKey($k)) { continue }
      $v = [System.Diagnostics.CounterSample]::Calculate($prev[$k], $cur[$k])
      if ($v -lt 0.1) { continue }
      if ($k -match "pid_(\d+)_.*engtype_(\w+)") {
        $p = [int]$matches[1]; $e = $matches[2]
        if (-not $names.ContainsKey($p)) {
          $n = (Get-Process -Id $p).ProcessName
          if (-not $n) { $n = "pid$p" }
          $names[$p] = $n
        }
        $kk = "$($names[$p])|$e"
        $agg[$kk] = [math]::Min(100, $agg[$kk] + $v)
      }
    }
    $o = @{}
    foreach ($k in $agg.Keys) { $o[$k] = [math]::Round($agg[$k], 1) }
    $t = [math]::Round(([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()) / 1000.0, 1)
    $line = (@{ t = $t; u = $o } | ConvertTo-Json -Compress)
    try { [Console]::Out.WriteLine($line); [Console]::Out.Flush() } catch { exit 0 }
  }
  $prev = $cur
  if ($names.Count -gt 500) { $names = @{} }
  Start-Sleep -Milliseconds 2000
}
