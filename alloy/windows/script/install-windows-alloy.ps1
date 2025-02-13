# Script to install Alloy for Windows
param (
    $DUPLO_LOGGING_PATHS,
    $DUPLO_METRICS_URL,
    $DUPLO_LOGGING_URL,
    $DUPLO_CLUSTER_NAME,
    $DUPLO_TENANT_NAME,
    $HOSTNAME
)

# Sets the default TLS version to 1.2 to avoid issues downloading the Alloy installer on some networks
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

Write-Host "Setting up Alloy"

if (-Not [bool](([System.Security.Principal.WindowsIdentity]::GetCurrent()).groups -match "S-1-5-32-544")) {
    Write-Host "ERROR: The script needs to be run with Administrator privileges"
    exit 1
}

# Validate required parameters
if ($DUPLO_LOGGING_PATHS -eq $null -or $DUPLO_LOGGING_PATHS -eq "") {
    Write-Host "ERROR: Required argument DUPLO_LOGGING_PATHS missing"
    exit 1
}

if ($DUPLO_METRICS_URL -eq $null -or $DUPLO_METRICS_URL -eq "") {
    Write-Host "ERROR: Required argument DUPLO_METRICS_URL missing"
    exit 1
}

if ($DUPLO_LOGGING_URL -eq $null -or $DUPLO_LOGGING_URL -eq "") {
    Write-Host "ERROR: Required argument DUPLO_LOGGING_URL missing"
    exit 1
}

if ($DUPLO_TENANT_NAME -eq $null -or $DUPLO_TENANT_NAME -eq "") {
    Write-Host "ERROR: Required argument DUPLO_TENANT_NAME missing"
    exit 1
}

if ($HOSTNAME -eq $null -or $HOSTNAME -eq "") {
    Write-Host "ERROR: Required argument HOSTNAME missing"
    exit 1
}

try {
    Write-Host "DUPLO_LOGGING_PATHS:" $DUPLO_LOGGING_PATHS
    Write-Host "DUPLO_METRICS_URL:" $DUPLO_METRICS_URL
    Write-Host "DUPLO_LOGGING_URL:" $DUPLO_LOGGING_URL
    Write-Host "DUPLO_CLUSTER_NAME:" $DUPLO_CLUSTER_NAME
    Write-Host "DUPLO_TENANT_NAME:" $DUPLO_TENANT_NAME
    Write-Host "HOSTNAME:" $HOSTNAME

    Write-Host "Downloading Alloy Windows Installer"
    $DOWNLOAD_URL = "https://github.com/grafana/alloy/releases/download/v1.4.3/alloy-installer-windows-amd64.exe.zip"
    $OUTPUT_ZIP_FILE = ".\alloy-installer-windows-amd64.exe.zip"
    $OUTPUT_FILE = ".\alloy-installer-windows-amd64.exe"
    $WORKING_DIR = Get-Location
    Invoke-WebRequest -Uri $DOWNLOAD_URL -OutFile $OUTPUT_ZIP_FILE -ErrorAction Stop
    Expand-Archive -LiteralPath $OUTPUT_ZIP_FILE -DestinationPath $WORKING_DIR.path -force -ErrorAction Stop
}
catch {
    Write-Host "ERROR: Failed to extract Alloy installer for Windows"
    Write-Error $_.Exception
    exit 1
}

# Install Alloy in silent mode
Write-Host "Installing Alloy for Windows"
$INSTALL_STDOUT_PATH = ".\install-stdout.txt"
$INSTALL_STDERR_PATH = ".\install-stderr.txt"
$INSTALL_PROC = Start-Process ".\alloy-installer-windows-amd64.exe" -ArgumentList "/S","/DISABLEREPORTING=yes" -Wait -PassThru -RedirectStandardOutput $INSTALL_STDOUT_PATH -RedirectStandardError $INSTALL_STDERR_PATH
if ($INSTALL_PROC.ExitCode -ne 0) {
    Write-Host "ERROR: Failed to install Alloy"
    Write-Host "Alloy Install STDOUT: $(Get-Content $INSTALL_STDOUT_PATH)"
    Write-Host "Alloy Install STDERR: $(Get-Content $INSTALL_STDERR_PATH)"
    exit $INSTALL_PROC.ExitCode
}

try {
    $CONFIG_FILE = ".\config.alloy"

    Write-Host "--- Retrieving Alloy config"
    $CONFIG_URI = "https://raw.githubusercontent.com/duplocloud/opentelemetry-release/refs/heads/main/alloy/windows/config.alloy"
    
    Invoke-WebRequest -Uri $CONFIG_URI -Outfile $CONFIG_FILE -ErrorAction Stop
    $content = Get-Content $CONFIG_FILE

    Write-Host "--- Replacing Alloy config params with arg values"
    $content = $content.Replace("{DUPLO_LOGGING_PATHS}", $DUPLO_LOGGING_PATHS)
    $content = $content.Replace("{DUPLO_METRICS_URL}", $DUPLO_METRICS_URL)
    $content = $content.Replace("{DUPLO_LOGGING_URL}", $DUPLO_LOGGING_URL)
    $content = $content.Replace("{DUPLO_CLUSTER_NAME}", $DUPLO_CLUSTER_NAME)
    $content = $content.Replace("{DUPLO_TENANT_NAME}", $DUPLO_TENANT_NAME)
    $content = $content.Replace("{HOSTNAME}", $HOSTNAME)
    $content | Set-Content $CONFIG_FILE

    $DEST_DIR = "C:\Program Files\GrafanaLabs\Alloy"
    Write-Host "--- Moving finalized Alloy config to $DEST_DIR\config.alloy"
    Move-Item $CONFIG_FILE "$DEST_DIR\config.alloy" -force -ErrorAction Stop
} catch {
    Write-Host "ERROR: Failed to retrieve Alloy config file"
    Write-Error $_.Exception
    exit 1
}

Write-Host "Alloy config file retrieved, configured, and installed successfully"

# Wait for service to initialize after first install
Write-Host "Wait for Alloy service to initialize"
Start-Sleep -s 5 -ErrorAction SilentlyContinue

# Restart Alloy to load new configuration
Write-Host "Restarting Alloy service"
Stop-Service -Name "Alloy" -ErrorAction SilentlyContinue
Start-Service -Name "Alloy" -ErrorAction SilentlyContinue

# Wait for service to startup after restart
Write-Host "Wait for Alloy service to initialize after restart"
Start-Sleep -s 10 -ErrorAction SilentlyContinue

# Show Alloy service status
Get-Service -Name "Alloy" | Select-Object Status, DisplayName
