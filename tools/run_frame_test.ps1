<#
.SYNOPSIS
  Runs a staged test script on the Steam Frame and saves the result for review.

.DESCRIPTION
  Copies a script from tools/ to the headset (LF line endings, no PowerShell pipe
  artefacts), waits -DelaySeconds so you can get into position, runs it with
  SECS=-Seconds, removes the script from the headset, and writes the output to
  data/test_runs/<timestamp>_<label>.txt (gitignored). Read-only on the headset
  apart from a temporary script in /tmp (the scripts themselves document anything
  else they create and delete).

.PARAMETER Script
  File name under tools/ to run. Default: frame_capture_test.sh (colour passthrough
  fps/thermal). Use frame_trackcam_probe.sh for the tracking-camera probe.

.PARAMETER HeadsetHost
  Default 'frame' (mDNS name; survives DHCP changes). Pass an IP if that fails.

.PARAMETER FetchDir
  Optional remote directory (for example /dev/shm/ptframes). After the script runs,
  its files are copied to data\frames\<timestamp>_<label>\ on the PC (gitignored) and
  the remote directory is ALWAYS deleted, even if the copy fails.

.EXAMPLE
  .\tools\run_frame_test.ps1 -Label T1-home-roomview-off -Script frame_trackcam_probe.sh -Seconds 12 -DelaySeconds 5
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Label,
    [string]$Script = "frame_capture_test.sh",
    [int]$Seconds = 20,
    [int]$DelaySeconds = 15,
    [string]$HeadsetHost = "frame",
    [string]$FetchDir = "",
    [string]$Key = "$HOME\.ssh\id_ed25519_frame"
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$src = Join-Path $PSScriptRoot $Script
if (-not (Test-Path $src)) { throw "No such script: $src" }
$outDir = Join-Path $repo "data\test_runs"
New-Item -ItemType Directory -Force $outDir | Out-Null
$safeLabel = $Label -replace '[^A-Za-z0-9_-]', '-'
$outFile = Join-Path $outDir ("{0}_{1}.txt" -f (Get-Date -Format "yyyyMMdd-HHmmss"), $safeLabel)
$remote = "/tmp/$Script"

# accept-new: first contact by hostname records the key (same LAN device); later changes still fail.
$sshOpts = @("-i", $Key, "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=10",
             "-o", "StrictHostKeyChecking=accept-new")
$target = "steamos@$HeadsetHost"

# Write an LF-only copy so bash never sees carriage returns.
$tmp = Join-Path $env:TEMP ("lf_" + $Script)
$text = [IO.File]::ReadAllText($src) -replace "`r`n", "`n"
[IO.File]::WriteAllText($tmp, $text, (New-Object Text.UTF8Encoding($false)))

Write-Host "Script: $Script   Label: $safeLabel   Duration: ${Seconds}s   Start delay: ${DelaySeconds}s   Host: $HeadsetHost"
& ssh @sshOpts $target "echo connected" | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Cannot reach $target. Wake the headset (wear it or press the power button), wait ~30 s, make sure it is on the same network, and retry. If 'frame' does not resolve, pass -HeadsetHost <ip> (Quick Settings > Wi-Fi shows it)."
}

& scp @sshOpts $tmp "${target}:$remote" | Out-Null
try {
    for ($i = $DelaySeconds; $i -gt 0; $i--) {
        Write-Host -NoNewline ("`rStarting in {0,3}s - get into position now...   " -f $i)
        Start-Sleep -Seconds 1
    }
    Write-Host "`rRunning...                                                  "
    $header = "script=$Script label=$safeLabel seconds=$Seconds delay=$DelaySeconds host=$HeadsetHost pc_time=$(Get-Date -Format o)"
    $result = & ssh @sshOpts $target "SECS=$Seconds bash $remote" 2>&1
    $exit = $LASTEXITCODE
    $report = @($header) + $result + @("exit_code=$exit")
    $report | Set-Content -Path $outFile -Encoding UTF8
    $report | ForEach-Object { Write-Host $_ }
    if ($FetchDir -and $FetchDir -match '^/(dev/shm|tmp)/[A-Za-z0-9_.-]+$') {
        $framesDir = Join-Path $repo ("data\frames\{0}_{1}" -f (Get-Date -Format "yyyyMMdd-HHmmss"), $safeLabel)
        New-Item -ItemType Directory -Force $framesDir | Out-Null
        & scp @sshOpts -r "${target}:${FetchDir}/*" $framesDir 2>&1 | Out-Null
        Write-Host ("Fetched {0} file(s) to {1}" -f @(Get-ChildItem $framesDir -File -ErrorAction SilentlyContinue).Count, $framesDir)
    }
    elseif ($FetchDir) { Write-Warning "Ignoring -FetchDir '$FetchDir': only /dev/shm/<name> or /tmp/<name> are allowed." }
}
finally {
    & ssh @sshOpts $target "rm -f $remote" | Out-Null
    if ($FetchDir -and $FetchDir -match '^/(dev/shm|tmp)/[A-Za-z0-9_.-]+$') { & ssh @sshOpts $target "rm -rf $FetchDir" | Out-Null }
    Remove-Item $tmp -ErrorAction SilentlyContinue
}
Write-Host ""
Write-Host "Saved: $outFile"
Write-Host "Now tell Copilot: 'results saved' - it will read data\test_runs."
