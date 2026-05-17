#
# MK7BoostGauge v3 - One-time SSH key setup on Windows PC.
#
# After running this, update_pi.ps1 won't ask for password every time.
# You enter the Pi password ONCE during setup, then never again.
#
# Usage (in PowerShell, in project directory):
#   .\setup_ssh_key.ps1
#
$ErrorActionPreference = "Stop"

$PiHost = "pi@boostgauge.local"
$KeyPath = "$HOME\.ssh\id_ed25519"

Write-Host "==> 1. Checking SSH key existence..."
if (-not (Test-Path $KeyPath)) {
    Write-Host "    Generating new ed25519 key (no passphrase)..."
    & ssh-keygen -t ed25519 -f $KeyPath -N '""' -C "mk7boostgauge-pc"
    Write-Host "    Key created at $KeyPath" -ForegroundColor Green
} else {
    Write-Host "    Key already exists at $KeyPath" -ForegroundColor Green
}

Write-Host ""
Write-Host "==> 2. Copying public key to Pi (enter Pi password ONCE)..."

$pubKey = (Get-Content "$KeyPath.pub" -Raw).Trim()
$cmd = "mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && grep -qxF '$pubKey' ~/.ssh/authorized_keys || echo '$pubKey' >> ~/.ssh/authorized_keys && echo 'Key installed'"

& ssh $PiHost $cmd

Write-Host ""
Write-Host "==> 3. Testing key auth (should NOT ask for password)..."
$test = & ssh -o BatchMode=yes -o ConnectTimeout=5 $PiHost "whoami" 2>&1
if ($test -eq "pi") {
    Write-Host "    SUCCESS! SSH key auth working." -ForegroundColor Green
    Write-Host ""
    Write-Host "    You can now run update_pi.ps1 without password prompts." -ForegroundColor Cyan
} else {
    Write-Host "    Key test output: $test" -ForegroundColor Yellow
    Write-Host "    If you see password prompts in future, re-run this script." -ForegroundColor Yellow
}
