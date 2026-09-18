$ErrorActionPreference = "Stop"


# ==================================================
# Paths
# ==================================================

$MonitoringDir = Split-Path -Parent $PSScriptRoot

$ComposeFile = Join-Path `
    $MonitoringDir `
    "docker-compose-monitoring.yml"

$EnvFile = Join-Path `
    $MonitoringDir `
    "email\email.env"

$AlertmanagerUrl = "http://localhost:9093"

$AlertmanagerConfig = Join-Path `
    $MonitoringDir `
    "alertmanager.yml"


# ==================================================
# Helper: Load email.env
# ==================================================

function Load-EnvFile {

    param (
        [string]$Path
    )

    if (-not (Test-Path $Path)) {
        throw "Environment file not found: $Path"
    }

    $config = @{}

    foreach ($rawLine in Get-Content $Path) {

        $line = $rawLine.Trim()

        if ([string]::IsNullOrWhiteSpace($line)) {
            continue
        }

        if ($line.StartsWith("#")) {
            continue
        }

        if (-not $line.Contains("=")) {
            continue
        }

        $parts = $line.Split("=", 2)

        $key = $parts[0].Trim()
        $value = $parts[1].Trim()

        if (
            $value.Length -ge 2 -and
            (
                (
                    $value.StartsWith('"') -and
                    $value.EndsWith('"')
                ) -or
                (
                    $value.StartsWith("'") -and
                    $value.EndsWith("'")
                )
            )
        ) {
            $value = $value.Substring(
                1,
                $value.Length - 2
            )
        }

        $config[$key] = $value
    }

    return $config
}


# ==================================================
# Load configuration
# ==================================================

$config = Load-EnvFile -Path $EnvFile

$emailEnabled = (
    $config["EMAIL_ENABLED"] `
    -eq "true"
)

$emailProvider = (
    $config["EMAIL_PROVIDER"]
)

if ([string]::IsNullOrWhiteSpace($emailProvider)) {
    $emailProvider = "custom"
}


Write-Host ""
Write-Host "========================================"
Write-Host " Monitoring Configuration Apply"
Write-Host "========================================"
Write-Host ""


# ==================================================
# 1. Validate Docker Compose
# ==================================================

Write-Host "[1/6] Validating Docker Compose configuration..."

docker compose `
    -f $ComposeFile `
    config | Out-Null

if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose configuration is invalid."
}

Write-Host "OK"


# ==================================================
# 2. Generate configuration
# ==================================================

Write-Host ""
Write-Host "[2/6] Generating Alertmanager configuration..."

docker compose `
    -f $ComposeFile `
    run `
    --rm `
    alertmanager-config-generator

if ($LASTEXITCODE -ne 0) {
    throw "Alertmanager configuration generation failed."
}

Write-Host "OK"


# ==================================================
# 3. Verify notification channel
# ==================================================

Write-Host ""
Write-Host "[3/6] Verifying notification channel..."

if (-not (Test-Path $AlertmanagerConfig)) {
    throw "Generated Alertmanager configuration not found."
}

$generatedConfig = Get-Content `
    $AlertmanagerConfig `
    -Raw


if ($emailEnabled) {

    if (
        -not $generatedConfig.Contains(
            "name: email-receiver"
        )
    ) {
        throw `
            "EMAIL_ENABLED=true but email-receiver was not generated."
    }

    if (
        -not $generatedConfig.Contains(
            "receiver: email-receiver"
        )
    ) {
        throw `
            "EMAIL_ENABLED=true but notification routes are not using email-receiver."
    }

    Write-Host "Email notifications: ENABLED"
    Write-Host "Email provider: $emailProvider"
    Write-Host "Notification channel: Email"

}
else {

    if (
        $generatedConfig.Contains(
            "name: email-receiver"
        )
    ) {
        throw `
            "EMAIL_ENABLED=false but email-receiver exists in generated configuration."
    }

    if (
        -not $generatedConfig.Contains(
            "receiver: critical-receiver"
        )
    ) {
        throw `
            "Critical route is not configured for Telegram."
    }

    if (
        -not $generatedConfig.Contains(
            "receiver: warning-receiver"
        )
    ) {
        throw `
            "Warning route is not configured for Telegram."
    }

    Write-Host "Email notifications: DISABLED"
    Write-Host "Notification channel: Telegram"
}


# ==================================================
# 4. Validate Alertmanager configuration
# ==================================================

Write-Host ""
Write-Host "[4/6] Validating Alertmanager configuration..."

docker exec `
    alertmanager `
    amtool check-config `
    /etc/alertmanager/alertmanager.yml

if ($LASTEXITCODE -ne 0) {
    throw `
        "Generated Alertmanager configuration is invalid."
}

Write-Host "OK"


# ==================================================
# 5. Reload Alertmanager
# ==================================================

Write-Host ""
Write-Host "[5/6] Reloading Alertmanager..."

$response = Invoke-WebRequest `
    -Method Post `
    -Uri "$AlertmanagerUrl/-/reload" `
    -UseBasicParsing

if ($response.StatusCode -ne 200) {
    throw "Alertmanager reload failed."
}

Write-Host "OK"


# ==================================================
# 6. Verify readiness
# ==================================================

Write-Host ""
Write-Host "[6/6] Verifying Alertmanager readiness..."

$statusResponse = Invoke-RestMethod `
    -Method Get `
    -Uri "$AlertmanagerUrl/api/v2/status"

if ($statusResponse.cluster.status -ne "ready") {
    throw "Alertmanager is not ready."
}

Write-Host "OK"


# ==================================================
# Final result
# ==================================================

Write-Host ""
Write-Host "========================================"
Write-Host " Configuration applied successfully"
Write-Host "========================================"

if ($emailEnabled) {

    Write-Host "Email: ENABLED"
    Write-Host "Provider: $emailProvider"
    Write-Host "Channel: Email"

}
else {

    Write-Host "Email: DISABLED"
    Write-Host "Channel: Telegram"
}

Write-Host "Alertmanager: READY"
Write-Host "========================================"
Write-Host ""