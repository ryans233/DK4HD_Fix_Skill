# Acceptance check 9.4 -- short-click latch.
#
#   powershell -ExecutionPolicy Bypass -File tools\click_test.ps1 -GameDir "D:\Games\DK4HD" `
#       -TraceAsi build\DK4HD_InputFix_trace.asi
#
# Sends 5/10/20/40/80 ms left clicks (12 per hold) with mouse_event to the game
# window, then matches the diagnostic log lines
#
#   latch msg=0x0201 hwnd=...
#   inject dev=0x... age=<ms>
#
# written by the trace build (-DDK4HD_TRACE_CLICKS, see skills.md 9.4) and
# reports the injection rate per hold time plus duplicate-down detection.
# Without -TraceAsi the script only sends the click train; judge the result in
# game by hand.

[CmdletBinding()]
param(
    [string]$GameDir = ".",
    [string]$Exe = "DK4HD_tc.exe",
    [int[]]$HoldMs = @(5, 10, 20, 40, 80),
    [int]$Repeats = 12,
    [int]$GapMs = 400,
    [int]$SettleSeconds = 12,
    [string]$TraceAsi = ""
)

$ErrorActionPreference = "Stop"
$dir = (Resolve-Path -LiteralPath $GameDir).Path
$exePath = Join-Path $dir $Exe
$asiPath = Join-Path $dir "DK4HD_InputFix.asi"
$logPath = Join-Path $dir "DK4HD_InputFix.log"
if (-not (Test-Path -LiteralPath $exePath)) { throw "$exePath not found" }

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Dk4Input {
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out Dk4Rect r);
    [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h, ref Dk4Point p);
    [DllImport("user32.dll")] public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, IntPtr extra);
    public struct Dk4Rect { public int Left, Top, Right, Bottom; }
    public struct Dk4Point { public int X, Y; }
}
"@

$backup = $null
$useTrace = $false
if ($TraceAsi) {
    $TraceAsi = (Resolve-Path -LiteralPath $TraceAsi).Path
    if (Test-Path -LiteralPath $asiPath) {
        $backup = "$asiPath.release.bak"
        Copy-Item -LiteralPath $asiPath -Destination $backup -Force
    }
    Copy-Item -LiteralPath $TraceAsi -Destination $asiPath -Force
    $useTrace = $true
}

Remove-Item -LiteralPath $logPath -Force -ErrorAction SilentlyContinue

$proc = $null
try {
    $proc = Start-Process -FilePath $exePath -WorkingDirectory $dir -PassThru
    Start-Sleep -Seconds $SettleSeconds
    if ($proc.HasExited) { throw "$Exe exited early (code $($proc.ExitCode))" }

    $proc.Refresh()
    $hwnd = $proc.MainWindowHandle
    if (-not $hwnd) { throw "no main window handle" }
    [void][Dk4Input]::SetForegroundWindow($hwnd)
    Start-Sleep -Milliseconds 700

    $rect = New-Object Dk4Input+Dk4Rect
    [void][Dk4Input]::GetClientRect($hwnd, [ref]$rect)
    $pt = New-Object Dk4Input+Dk4Point
    $pt.X = [int](($rect.Right - $rect.Left) / 2)
    $pt.Y = [int](($rect.Bottom - $rect.Top) / 2)
    [void][Dk4Input]::ClientToScreen($hwnd, [ref]$pt)
    [void][Dk4Input]::SetCursorPos($pt.X, $pt.Y)
    Start-Sleep -Milliseconds 300

    Write-Host ("Sending {0} clicks per hold time: {1} ms" -f $Repeats, ($HoldMs -join "/"))
    foreach ($hold in $HoldMs) {
        for ($i = 0; $i -lt $Repeats; $i++) {
            [Dk4Input]::mouse_event(0x0002, 0, 0, 0, [IntPtr]::Zero)   # LEFTDOWN
            Start-Sleep -Milliseconds $hold
            [Dk4Input]::mouse_event(0x0004, 0, 0, 0, [IntPtr]::Zero)   # LEFTUP
            Start-Sleep -Milliseconds $GapMs
        }
        Write-Host "  sent hold=$hold"
    }
    Start-Sleep -Milliseconds 800
} finally {
    if ($proc -and -not $proc.HasExited) { $proc.Kill(); [void]$proc.WaitForExit(5000) }
    if ($useTrace) {
        if ($backup) {
            Copy-Item -LiteralPath $backup -Destination $asiPath -Force
            Remove-Item -LiteralPath $backup -Force
        } else {
            Remove-Item -LiteralPath $asiPath -Force
        }
    }
}

if (-not $useTrace) {
    Write-Host "Trace build not supplied (-TraceAsi); click train finished, verify the response in game."
    exit 0
}

$lines = @()
if (Test-Path -LiteralPath $logPath) {
    $lines = @(Get-Content -LiteralPath $logPath | Where-Object { $_ -match '^(latch|inject) ' })
}
$latches = @($lines | Where-Object { $_ -like 'latch *' })
$injects = @($lines | Where-Object { $_ -like 'inject *' })
if ($latches.Count -eq 0) {
    throw "no latch lines in the log; was the trace build (-DDK4HD_TRACE_CLICKS) loaded?"
}

# Sequential pairing: an inject consumes the oldest latch that is still pending.
$pending = New-Object 'System.Collections.Generic.Queue[int]'
$credited = New-Object bool[] $latches.Count
$ages = New-Object 'System.Collections.Generic.List[int]'
$duplicates = 0
$nextLatch = 0
foreach ($line in $lines) {
    if ($line -like 'latch *') {
        $pending.Enqueue($nextLatch)
        $nextLatch++
    } else {
        if ($pending.Count -eq 0) {
            $duplicates++
        } else {
            $idx = $pending.Dequeue()
            $credited[$idx] = $true
            if ($line -match 'age=(\d+)') { $ages.Add([int]$Matches[1]) }
        }
    }
}

$expected = $HoldMs.Count * $Repeats
$ok = $true
Write-Host ""
for ($g = 0; $g -lt $HoldMs.Count; $g++) {
    $from = $g * $Repeats
    $to = [math]::Min($from + $Repeats, $credited.Count) - 1
    $hits = 0
    if ($to -ge $from) { $hits = @($credited[$from..$to] | Where-Object { $_ }).Count }
    $need = [math]::Min(10, $Repeats)
    $verdict = if ($hits -ge $need) { "PASS" } else { $ok = $false; "FAIL" }
    Write-Host ("hold={0,3} ms  {1}/{2} injected  [{3}]" -f $HoldMs[$g], $hits, $Repeats, $verdict)
}

$medianAge = 0
if ($ages.Count -gt 0) {
    $sortedAges = @($ages | Sort-Object)
    $medianAge = $sortedAges[[int][math]::Floor($sortedAges.Count / 2)]
}
Write-Host ("latches={0} (expected {1})  injections={2}  duplicates={3}  median age={4} ms" -f `
        $latches.Count, $expected, $injects.Count, $duplicates, $medianAge)

if ($latches.Count -lt $expected) { Write-Host "[FAIL] some clicks never produced a window message"; $ok = $false }
if ($duplicates -gt 0) { Write-Host "[FAIL] $duplicates injected down edge(s) without a pending click"; $ok = $false }

if ($ok) {
    Write-Host "[PASS] short clicks are latched and injected exactly once"
    exit 0
}
exit 1
