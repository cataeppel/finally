# Stop and remove the FinAlly container. The data volume is preserved.
# Idempotent: safe to re-run.
$ErrorActionPreference = "Stop"

$ContainerName = "finally_agents"

docker info *>$null
if ($LASTEXITCODE -ne 0) {
    Write-Error "Docker does not appear to be running."
}

$existing = docker ps -aq -f "name=^$ContainerName$"
if ($existing) {
    Write-Host "Stopping FinAlly..."
    docker rm -f $ContainerName | Out-Null
    Write-Host "FinAlly stopped. Your portfolio data is preserved in the finally-data-agents volume."
} else {
    Write-Host "FinAlly is not running."
}
