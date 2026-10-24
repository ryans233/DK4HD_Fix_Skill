# Acceptance check 9.2 -- frame pacing via PresentMon.
#
#   powershell -ExecutionPolicy Bypass -File tools\measure_frames.ps1 -GameDir "D:\Games\DK4HD"
#
# Launches the game, lets it settle (loading/intro stalls excluded), records a
# 15 s PresentMon capture with --v1_metrics and prints msBetweenPresents
# percentiles. Fails when the frame time is quantized instead of smooth.
#
# PresentMon is downloaded once from the official Intel release when -PresentMon
# is not supplied.

[CmdletBinding()]
param(
    [string]$GameDir = ".",
    [string]$Exe = "DK4HD_tc.exe",
    [int]$SettleSeconds = 18,
    [int]$Seconds = 15,
    [string]$PresentMon = "",
    [string]$Csv = "",
    [double]$P50Min = 32.0,
    [double]$P50Max = 34.5,
    [double]$P95Max = 40.0
)

$ErrorActionPreference = "Stop"
$dir = (Resolve-Path -LiteralPath $GameDir).Path
$exePath = Join-Path $dir $Exe
if (-not (Test-Path -LiteralPath $exePath)) { throw "$exePath not found" }

if (-not $Csv) { $Csv = Join-Path ([IO.Path]::GetTempPath()) "dk4hd_frames_$([IO.Path]::GetFileNameWithoutExtension($Exe)).csv" }

if (-not $PresentMon) {
    $candidate = Join-Path ([IO.Path]::GetTempPath()) "PresentMon-2.6.0-x64.exe"
    if (-not (Test-Path -LiteralPath $candidate)) {
        Write-Host "Downloading PresentMon 2.6.0 ..."
        Invoke-WebRequest -UseBasicParsing -OutFile $candidate `
            -Uri "https://github.com/GameTechDev/PresentMon/releases/download/v2.6.0/PresentMon-2.6.0-x64.exe"
    }
    $PresentMon = $candidate
}
if (-not (Test-Path -LiteralPath $PresentMon)) { throw "PresentMon not found: $PresentMon" }

function Get-Percentile {
    param([double[]]$Values, [double]$P)
    $sorted = @($Values | Sort-Object)
    if ($sorted.Count -eq 0) { return 0 }
    $k = ($sorted.Count - 1) * $P / 100.0
    $f = [math]::Floor($k)
    $c = [math]::Min($f + 1, $sorted.Count - 1)
    return $sorted[$f] + ($sorted[$c] - $sorted[$f]) * ($k - $f)
}

Remove-Item -LiteralPath $Csv -Force -ErrorAction SilentlyContinue
$proc = Start-Process -FilePath $exePath -WorkingDirectory $dir -PassThru
Start-Sleep -Seconds $SettleSeconds
if ($proc.HasExited) { throw "$Exe exited before capture (code $($proc.ExitCode))" }

try {
    & $PresentMon --process_id $proc.Id --output_file $Csv --v1_metrics --no_console_stats `
        --timed $Seconds --terminate_after_timed | Out-Null
} finally {
    if (-not $proc.HasExited) { $proc.Kill(); [void]$proc.WaitForExit(5000) }
}

if (-not (Test-Path -LiteralPath $Csv)) { throw "no capture written to $Csv" }
$rows = @(Import-Csv -LiteralPath $Csv)
$ms = @($rows | Where-Object { $_.msBetweenPresents } | ForEach-Object { [double]$_.msBetweenPresents })
if ($ms.Count -lt 60) { throw "only $($ms.Count) frames captured; is the game presenting?" }

$syncValues = @($rows | Where-Object { $_.SyncInterval } | ForEach-Object { $_.SyncInterval } | Sort-Object -Unique)
$p5 = Get-Percentile $ms 5
$p50 = Get-Percentile $ms 50
$p95 = Get-Percentile $ms 95
$max = ($ms | Measure-Object -Maximum).Maximum
$slowBand = @($ms | Where-Object { $_ -ge 45.0 -and $_ -le 49.5 }).Count / $ms.Count
$longFrames = @($ms | Where-Object { $_ -gt 100.0 }).Count
$fps = 1000.0 / $p50

Write-Host ("frames={0}  p5={1:N2}ms  p50={2:N2}ms  p95={3:N2}ms  max={4:N1}ms  fps(p50)={5:N1}" -f `
        $ms.Count, $p5, $p50, $p95, $max, $fps)
Write-Host ("SyncInterval={0}  45-49.5ms share={1:P1}  frames>100ms={2}" -f `
        ($syncValues -join ","), $slowBand, $longFrames)

$ok = $true
if ($syncValues.Count -ne 1 -or $syncValues[0] -ne "1") { Write-Host "[FAIL] SyncInterval is not 1"; $ok = $false }
if ($p50 -lt $P50Min -or $p50 -gt $P50Max) { Write-Host "[FAIL] p50 outside $P50Min..$P50Max ms"; $ok = $false }
if ($p95 -gt $P95Max) { Write-Host "[FAIL] p95 above $P95Max ms"; $ok = $false }
if ($slowBand -gt 0.01) { Write-Host "[FAIL] more than 1% of frames land in the 45-49.5 ms quantization band"; $ok = $false }
if ($fps -lt 28.0 -or $fps -gt 32.0) { Write-Host "[FAIL] p50 frame rate is $([math]::Round($fps,1)) fps, expected ~30"; $ok = $false }

if ($ok) {
    Write-Host "[PASS] frame pacing is smooth (no 31.25/46.875 ms quantization)"
    exit 0
}
Write-Host "Capture kept at $Csv"
exit 1
