# Build (if needed) and run the FinAlly container. Idempotent: safe to re-run.
# Usage: .\scripts\start_windows.ps1 [--build]
$ErrorActionPreference = "Stop"

$ContainerName = "finally_agents"
$ImageName     = "finally_agents"
$VolumeName    = "finally-data-agents"
$Port          = if ($env:PORT) { $env:PORT } else { 8000 }

Set-Location (Split-Path $PSScriptRoot)

docker info *>$null
if ($LASTEXITCODE -ne 0) {
    Write-Error "Docker does not appear to be running. Start Docker Desktop and retry."
}

# Build if the image is missing or --build was passed
$shouldBuild = $args -contains "--build"
if (-not $shouldBuild) {
    docker image inspect $ImageName *>$null
    if ($LASTEXITCODE -ne 0) { $shouldBuild = $true }
}
if ($shouldBuild) {
    Write-Host "Building Docker image..."
    docker build -t $ImageName .
    if ($LASTEXITCODE -ne 0) { Write-Error "Docker build failed." }
}

# Remove any existing container with this name (running or stopped)
$existing = docker ps -aq -f "name=^$ContainerName$"
if ($existing) {
    Write-Host "Removing existing container..."
    docker rm -f $ContainerName | Out-Null
}

# Pass .env through if present (it is never baked into the image)
$envArgs = @()
if (Test-Path .env) {
    $envArgs = @("--env-file", ".env")
} else {
    Write-Host "Note: no .env found; AI chat needs OPENROUTER_API_KEY (see .env.example)."
}

Write-Host "Starting FinAlly..."
docker run -d --name $ContainerName -p "${Port}:8000" -v "${VolumeName}:/app/db" @envArgs $ImageName | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Error "Failed to start the container." }

$Url = "http://localhost:$Port"

# Wait for the app to answer before announcing it
Write-Host -NoNewline "Waiting for FinAlly to become healthy"
for ($i = 0; $i -lt 30; $i++) {
    try {
        Invoke-WebRequest -Uri "$Url/api/health" -TimeoutSec 2 -UseBasicParsing | Out-Null
        Write-Host " ready."
        break
    } catch {
        Write-Host -NoNewline "."
        Start-Sleep -Seconds 1
    }
}
Write-Host ""
Write-Host "FinAlly is running at $Url"
Write-Host ""

Start-Process $Url
