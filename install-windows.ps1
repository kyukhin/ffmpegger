<#
.SYNOPSIS
Sets up the conversion worker on this Windows machine: Python and ffmpeg, the
password for the library share, and a task that starts worker.py at logon.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File install-windows.ps1 -Library \\192.168.1.1\lib

.EXAMPLE
powershell -ExecutionPolicy Bypass -File install-windows.ps1 -Uninstall -Library \\192.168.1.1\lib

.NOTES
Run it again after moving the folder, to change the share or -Priority; it replaces the task.
-Priority low lets games go first, but Windows then converts on the efficiency cores only.
The password is typed into cmdkey and kept in Windows Credential Manager, never in a file.
See README.md.
#>
param(
    [Parameter(Mandatory = $true)][string]$Library,
    [string]$User = "kyukhin",
    [ValidateSet("normal", "low")][string]$Priority = "normal",
    [switch]$Uninstall
)
$ErrorActionPreference = "Stop"
$TaskName = "ffmpegger worker"
$Repo = $PSScriptRoot
$Library = $Library.TrimEnd("\")
$Server = ($Library -replace "^\\\\", "").Split("\")[0]

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    cmdkey /delete:$Server | Out-Null
    Write-Host "Removed the task and the saved password for $Server. Python and ffmpeg stay."
    exit 0
}

function Update-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
}

function Find-Python {
    # The py launcher comes with python.org Python; plain "python" may be the Store stub
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        $exe = & $py.Source -3 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $exe) { return $exe.Trim() }
    }
    return $null
}

# 1. Python and ffmpeg. PATH as of now, not as of when this window opened
Update-Path
$python = Find-Python
if (-not $python) {
    Write-Host "Installing Python..."
    winget install --id Python.Python.3.13 -e --accept-source-agreements --accept-package-agreements
    Update-Path
    $python = Find-Python
    if (-not $python) { throw "Python is installed but not found yet: open a new PowerShell and run this again" }
}
$pythonw = Join-Path (Split-Path $python) "pythonw.exe"
Write-Host "Python: $python"

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host "Installing ffmpeg..."
    winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
    Update-Path
    if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { throw "ffmpeg is installed but not found yet: open a new PowerShell and run this again" }
}
Write-Host "ffmpeg: $((Get-Command ffmpeg).Source)"

# 2. The share's password, typed at cmdkey's own prompt
if (-not (Test-Path $Library)) {
    Write-Host "Password of $User for $Server (the Samba password):"
    cmdkey /add:$Server /user:$User /pass
    # Windows may keep the failed attempt from just before for a few seconds
    $ok = $false
    foreach ($i in 1..10) {
        if (Test-Path $Library) { $ok = $true; break }
        Start-Sleep -Seconds 2
    }
    # Not there still: let Windows say why (wrong password, another login to the server, ...)
    if (-not $ok) { Get-ChildItem $Library -ErrorAction Stop | Out-Null }
}
Write-Host "Library: $Library is reachable"

# 3. The worker, started at logon and restarted if it dies
$action = New-ScheduledTaskAction -Execute $pythonw -WorkingDirectory $Repo `
    -Argument "`"$Repo\worker.py`" --library `"$Library`" --priority $Priority"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 3
$log = Join-Path $env:LOCALAPPDATA "ffmpegger\worker.log"
Write-Host "Task '$TaskName' is $((Get-ScheduledTask -TaskName $TaskName).State). Log: $log"
if (Test-Path $log) { Get-Content $log -Tail 3 }
