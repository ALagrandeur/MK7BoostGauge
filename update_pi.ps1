#
# MK7BoostGauge v3 - One-command Pi update from PC.
#
# What it does:
#   1. SSH to Pi
#   2. cd ~/MK7BoostGauge
#   3. git pull (Pi pulls from GitHub directly via its WiFi)
#   4. systemctl restart boostgauge-daemon
#
# Pre-requisites:
#   - Pi reachable via WiFi at boostgauge.local
#   - Pi has internet (to git pull)
#   - SSH key auth recommended (run setup_ssh_key.ps1 once)
#
# Usage from PowerShell, in this directory:
#   .\update_pi.ps1
#
$ErrorActionPreference = "Stop"

$PiHost = "pi@boostgauge.local"

function Say($msg)  { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }
function PrintOk($msg)   { Write-Host "    [OK] $msg" -ForegroundColor Green }
function PrintFail($msg) { Write-Host "    [FAIL] $msg" -ForegroundColor Red }

Say "MK7BoostGauge - PC -> Pi update via SSH"

# Quick reachability check
Say "1/3  Pinging Pi..."
$reachable = Test-Connection -ComputerName "boostgauge.local" -Count 1 -Quiet -TimeoutSeconds 3
if (-not $reachable) {
    PrintFail "Pi not reachable at boostgauge.local"
    PrintFail "Check WiFi + Pi powered on"
    exit 1
}
PrintOk "Pi reachable"

Say "2/3  Running git pull on Pi..."
& ssh $PiHost "cd ~/MK7BoostGauge && git pull 2>&1"
if ($LASTEXITCODE -ne 0) {
    PrintFail "git pull failed (check SSH access + Pi has internet)"
    exit 1
}
PrintOk "git pull successful"

Say "3/3  Restarting boostgauge-daemon on Pi..."
& ssh $PiHost "sudo systemctl restart boostgauge-daemon && sleep 2 && systemctl is-active boostgauge-daemon"
if ($LASTEXITCODE -ne 0) {
    PrintFail "Service restart failed"
    PrintFail "Check: ssh pi@boostgauge.local 'journalctl -u boostgauge-daemon -n 30'"
    exit 1
}
PrintOk "Service active"

Say "Update complete!"
Write-Host ""
Write-Host "    Refresh your browser tab to see UI changes (Ctrl+F5)" -ForegroundColor Cyan
Write-Host ""
