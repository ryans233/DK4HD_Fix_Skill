# Acceptance check 9.1 -- startup and hook installation for all four executables.
#
#   powershell -ExecutionPolicy Bypass -File tools\check_startup.ps1 -GameDir "D:\Games\DK4HD"
#
# For each exe: launch it, wait, confirm the process survives, then verify the
# log lines required by skills.md section 5. Exit code 0 = all checks passed.

[CmdletBinding()]
param(
    [string]$GameDir = ".",
    [int]$Seconds = 8,
    [string[]]$Exe = @("DK4HD_tc.exe", "DK4HD_kr.exe", "DK4HD_jp.exe", "DK4HD_sc.exe")
)

$ErrorActionPreference = "Stop"
$dir = (Resolve-Path -LiteralPath $GameDir).Path
$logPath = Join-Path $dir "DK4HD_InputFix.log"
$failures = 0

function Remove-LogFile {
    for ($i = 0; $i -lt 20; $i++) {
        try {
            if (Test-Path -LiteralPath $logPath) { Remove-Item -LiteralPath $logPath -Force }
            return
        } catch { Start-Sleep -Milliseconds 250 }
    }
}

function Read-LogFile {
    for ($i = 0; $i -lt 20; $i++) {
        try {
            if (Test-Path -LiteralPath $logPath) {
                return (Get-Content -LiteralPath $logPath -Raw)
            }
        } catch { }
        Start-Sleep -Milliseconds 250
    }
    return ""
}

foreach ($name in $Exe) {
    $exePath = Join-Path $dir $name
    if (-not (Test-Path -LiteralPath $exePath)) {
        Write-Host "[SKIP] $name (not found in $dir)"
        continue
    }

    Remove-LogFile
    $proc = Start-Process -FilePath $exePath -WorkingDirectory $dir -PassThru
    Start-Sleep -Seconds $Seconds
    $alive = -not $proc.HasExited
    if ($alive) {
        $proc.Kill()
        [void]$proc.WaitForExit(5000)
    }

    $log = Read-LogFile
    $checks = [ordered]@{
        "process alive"        = $alive
        "InitializeASI line"   = [bool]($log -match "(?m)^InitializeASI exe=$([regex]::Escape($name)) base=0x?[0-9a-fA-F]+")
        "qpc init"             = [bool]($log -match "qpcFreq=\d+ ticksPerMs=[1-9]\d*")
        "time/mouse located"   = [bool]($log -match "(?m)^time=0x?[0-9a-fA-F]+ mouse=0x?[0-9a-fA-F]+ \((scan|rva)\)")
        "mouse poll hooked"    = [bool]($log -match "(?m)^mouse poll hook: 1\r?$")
        "time hook"            = [bool]($log -match "(?m)^hook time: 0\r?$")
        "PeekMessageA hook"    = [bool]($log -match "(?m)^hook PeekMessageA: 0\r?$")
        "GetDeviceState hooked" = [bool]($log -match "(?m)^hooked mouse GetDeviceState dev=0x?[0-9a-fA-F]+ vt=0x?[0-9a-fA-F]+ real=0x?[0-9a-fA-F]+")
    }

    $bad = @($checks.Keys | Where-Object { -not $checks[$_] })
    if ($bad.Count -eq 0) {
        Write-Host "[PASS] $name"
    } else {
        $failures++
        Write-Host "[FAIL] $name : missing $($bad -join ', ')"
        if ($log) { Write-Host ($log.TrimEnd() -split "`r?`n" | ForEach-Object { "       | $_" } | Out-String).TrimEnd() }
    }
}

if ($failures -gt 0) {
    Write-Host "`n$failures executable(s) failed the startup check."
    exit 1
}
Write-Host "`nAll executables passed the startup check."
exit 0
