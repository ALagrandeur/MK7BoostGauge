#
# MK7BoostGauge — One-command update from PC to Pi via USB.
#
# Workflow:
#   1. PC pulls latest from GitHub (PC has internet)
#   2. PC packs the project into a tarball
#   3. PC pushes tarball to Pi via USB (scp)
#   4. Pi extracts + runs pip install if needed + restarts service
#
# Usage (in PowerShell, from this directory):
#   .\update_pi.ps1
#
# Prerequisites (one-time):
#   - Pi has USB gadget enabled (see pi_setup/enable_usb_gadget.sh)
#   - USB cable plugged: Pi DATA port -> PC
#   - (Recommended) SSH key auth set up — see setup_ssh_key.ps1
#   - tar command available (built into Windows 10+)
#
# What gets pushed:
#   app/, pi_setup/, tests/, requirements.txt, config.example.json,
#   preview_on_pc.py, README.md, INSTALL.md, PROCEDURE.txt
#
# What does NOT get touched on Pi:
#   /var/lib/boostgauge/config.json  (user's saved config — preserved)
#   .venv/                            (Python virtualenv)
#   .git/                             (no git op on Pi)
#

$ErrorActionPreference = "Stop"

# ---- Configuration ----
$PiHost     = "pi@boostgauge.local"
$PiHostAlt  = "pi@raspberrypi.local"   # fallback hostname
$PiPath     = "/home/pi/MK7BoostGauge"
$LocalPath  = $PSScriptRoot
$TarballPath = "$env:TEMP\mk7boost_update.tar.gz"

# ---- Pretty print ----
function Say($msg, $color = "Cyan") {
  Write-Host ""
  Write-Host "==> $msg" -ForegroundColor $color
}
function Ok($msg)   { Write-Host "    [OK] $msg" -ForegroundColor Green }
function Fail($msg) { Write-Host "    [FAIL] $msg" -ForegroundColor Red }

# ---- Detect reachable Pi host ----
function Get-ReachablePi {
  foreach ($host_ in @($PiHost, $PiHostAlt)) {
    $h = ($host_ -split "@")[-1]
    $reachable = Test-Connection -ComputerName $h -Count 1 -Quiet -TimeoutSeconds 2
    if ($reachable) {
      return $host_
    }
  }
  return $null
}

# ============================================================
#  Pre-flight checks
# ============================================================

Say "MK7BoostGauge update — PC -> Pi via USB"

# Verify tools available
foreach ($tool in @("git", "tar", "scp", "ssh")) {
  if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
    Fail "$tool not found in PATH. Install Git for Windows + OpenSSH client."
    exit 1
  }
}
Ok "Required tools present (git, tar, scp, ssh)"

# Verify we're in the repo dir
if (-not (Test-Path "$LocalPath\.git")) {
  Fail "Not a git repo: $LocalPath. Run this script from inside the project folder."
  exit 1
}
Ok "Local repo: $LocalPath"

# Find reachable Pi
Say "Checking Pi reachability via USB..."
$Pi = Get-ReachablePi
if (-not $Pi) {
  Fail "Pi not reachable as boostgauge.local or raspberrypi.local."
  Fail "Check:"
  Fail "  1. USB cable plugged to Pi DATA port (NOT power port)"
  Fail "  2. Pi booted (LED green stable)"
  Fail "  3. PC has 'USB Ethernet/RNDIS Gadget' in Device Manager"
  Fail "  4. Or use IP directly: edit `$PiHost in this script"
  exit 1
}
Ok "Pi reachable at: $Pi"

# ============================================================
#  Step 1 — Pull latest from GitHub
# ============================================================

Say "1/4  Pulling latest from GitHub on PC..."
Set-Location $LocalPath
git pull
Ok "Local repo updated"
$head = git rev-parse --short HEAD
Ok "HEAD: $head"

# ============================================================
#  Step 2 — Pack project
# ============================================================

Say "2/4  Packing project files..."
if (Test-Path $TarballPath) { Remove-Item $TarballPath }
# Use tar built into Windows 10+
tar -czf $TarballPath `
    -C $LocalPath `
    app pi_setup tests requirements.txt config.example.json `
    preview_on_pc.py update_pi.ps1 update_pi.sh `
    README.md INSTALL.md PROCEDURE.txt 2>$null
if (-not (Test-Path $TarballPath)) {
  Fail "Tarball creation failed"
  exit 1
}
$size = (Get-Item $TarballPath).Length
Ok ("Tarball: {0:N1} KB" -f ($size / 1KB))

# ============================================================
#  Step 3 — Push to Pi
# ============================================================

Say "3/4  Pushing to Pi via USB..."
& scp $TarballPath "${Pi}:/tmp/mk7boost_update.tar.gz"
if ($LASTEXITCODE -ne 0) {
  Fail "scp failed (check SSH access)"
  Remove-Item $TarballPath
  exit 1
}
Ok "Tarball pushed to /tmp on Pi"

# ============================================================
#  Step 4 — Extract + pip + restart on Pi
# ============================================================

Say "4/4  Extracting + restarting service on Pi..."
$remoteCmd = @"
set -e
cd $PiPath
tar -xzf /tmp/mk7boost_update.tar.gz
rm /tmp/mk7boost_update.tar.gz
echo "    [pip install]"
.venv/bin/pip install -q -r requirements.txt --upgrade 2>&1 | tail -3 || true
echo "    [restart service]"
sudo systemctl restart boostgauge
sleep 2
echo "    [service status]"
systemctl is-active boostgauge && echo "    >>> OK: service active" || echo "    >>> FAIL: service inactive"
"@
& ssh $Pi $remoteCmd
if ($LASTEXITCODE -ne 0) {
  Fail "Remote commands failed on Pi"
  Remove-Item $TarballPath
  exit 1
}

# ============================================================
#  Cleanup
# ============================================================

Remove-Item $TarballPath

Say "Update complete!" "Green"
Write-Host ""
Write-Host "    On your phone (still connected to MK7-BoostGauge WiFi):" -ForegroundColor Cyan
Write-Host "      http://192.168.4.1" -ForegroundColor White
Write-Host ""
Write-Host "    Or via USB from PC browser:" -ForegroundColor Cyan
Write-Host "      http://boostgauge.local" -ForegroundColor White
Write-Host ""
