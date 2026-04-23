#Requires -RunAsAdministrator
param(
    [Parameter(Mandatory=$true)]
    [string]$TargetIP,
    [int]$Port = 11434
)

Write-Host "[1/4] Getting Docker gateway IP..." -ForegroundColor Cyan
$dockerGateway = docker compose exec backend getent hosts host.docker.internal 2>$null | ForEach-Object { ($_ -split '\s+')[0] }
if (-not $dockerGateway) {
    Write-Host "  Could not get gateway from running container, using default 192.168.65.254" -ForegroundColor Yellow
    $dockerGateway = "192.168.65.254"
} else {
    Write-Host "  Gateway: $dockerGateway" -ForegroundColor Green
}

Write-Host "[2/4] Resetting portproxy rules..." -ForegroundColor Cyan
netsh interface portproxy reset | Out-Null

Write-Host "[3/4] Adding portproxy rules -> $TargetIP`:$Port" -ForegroundColor Cyan
netsh interface portproxy add v4tov4 listenport=$Port listenaddress=0.0.0.0 connectport=$Port connectaddress=$TargetIP | Out-Null
netsh interface portproxy add v4tov4 listenport=$Port listenaddress=$dockerGateway connectport=$Port connectaddress=$TargetIP | Out-Null

Write-Host "[4/4] Ensuring firewall rule..." -ForegroundColor Cyan
if (-not (Get-NetFirewallRule -DisplayName "Ollama Docker Forward" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName "Ollama Docker Forward" -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow | Out-Null
    Write-Host "  Firewall rule added" -ForegroundColor Green
} else {
    Write-Host "  Firewall rule already exists" -ForegroundColor Green
}

Write-Host "`nCurrent rules:" -ForegroundColor Cyan
netsh interface portproxy show all

Write-Host "`nTest from container:" -ForegroundColor Cyan
docker compose exec backend python -c "import urllib.request; r = urllib.request.urlopen('http://host.docker.internal:$Port/api/tags', timeout=5); print('OK:', r.read()[:100])"
