# Acceptance check 9.3 -- hottest-thread CPU usage.
#
#   powershell -ExecutionPolicy Bypass -File tools\measure_cpu.ps1 -GameDir "D:\Games\DK4HD"
#
# Samples Win32_PerfFormattedData_PerfProc_Thread while the game idles at its
# front end and reports the busiest thread. The counter is per-core: 100 means
# one fully busy logical processor. The unfixed build spins one thread at ~100%.

[CmdletBinding()]
param(
    [string]$GameDir = ".",
    [string]$Exe = "DK4HD_tc.exe",
    [int]$SettleSeconds = 10,
    [int]$Samples = 6,
    [int]$IntervalMs = 1000,
    [double]$MaxHotThreadOneCore = 10.0
)

$ErrorActionPreference = "Stop"
$dir = (Resolve-Path -LiteralPath $GameDir).Path
$exePath = Join-Path $dir $Exe
if (-not (Test-Path -LiteralPath $exePath)) { throw "$exePath not found" }

$proc = Start-Process -FilePath $exePath -WorkingDirectory $dir -PassThru
try {
    Start-Sleep -Seconds $SettleSeconds
    if ($proc.HasExited) { throw "$Exe exited early (code $($proc.ExitCode))" }

    $peak = @{}
    for ($i = 0; $i -lt $Samples; $i++) {
        Get-CimInstance Win32_PerfFormattedData_PerfProc_Thread -Filter "IDProcess=$($proc.Id)" -ErrorAction SilentlyContinue |
            ForEach-Object {
                if ($_.Name -eq "_Total") { return }
                $value = [double]$_.PercentProcessorTime
                if (-not $peak.ContainsKey($_.Name) -or $peak[$_.Name] -lt $value) { $peak[$_.Name] = $value }
            }
        Start-Sleep -Milliseconds $IntervalMs
    }
} finally {
    if (-not $proc.HasExited) { $proc.Kill(); [void]$proc.WaitForExit(5000) }
}

if ($peak.Count -eq 0) { throw "no thread samples collected" }

$top = $peak.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 3
foreach ($t in $top) {
    Write-Host ("{0,-28} {1,6:N1}% of one core" -f $t.Key, $t.Value)
}

$hottest = ($top | Select-Object -First 1).Value
if ($hottest -le $MaxHotThreadOneCore) {
    Write-Host "[PASS] hottest thread $([math]::Round($hottest,1))% <= $MaxHotThreadOneCore% of one core"
    exit 0
}
Write-Host "[FAIL] hottest thread $([math]::Round($hottest,1))% of one core; the time-source spin is probably still active"
exit 1
