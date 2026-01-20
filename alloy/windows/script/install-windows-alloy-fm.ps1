# Script to install Alloy for Windows
param (
    $DUPLO_CLUSTER_NAME,
    $DUPLO_TENANT_NAME,
    $HOSTNAME,
    $ENVIRONMENT,
    $GCLOUD_HOSTED_METRICS_URL,
    $GCLOUD_HOSTED_METRICS_ID,
    $GCLOUD_HOSTED_LOGS_URL,
    $GCLOUD_HOSTED_LOGS_ID,
    $GCLOUD_FM_URL,
    $GCLOUD_FM_POLL_FREQUENCY,
    $GCLOUD_FM_HOSTED_ID,
    $GCLOUD_RW_API_KEY
)

# Sets the default TLS version to 1.2 to avoid issues downloading the Alloy installer on some networks
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

Write-Host "Setting up Alloy"

if (-Not [bool](([System.Security.Principal.WindowsIdentity]::GetCurrent()).groups -match "S-1-5-32-544")) {
    Write-Host "ERROR: The script needs to be run with Administrator privileges"
    exit 1
}

# Validate required parameters
if ($GCLOUD_FM_URL -eq $null -or $GCLOUD_FM_URL -eq "") {
    Write-Host "ERROR: Required argument GCLOUD_FM_URL missing"
    exit 1
}

if ($ENVIRONMENT -eq $null -or $ENVIRONMENT -eq "") {
    Write-Host "ERROR: Required argument ENVIRONMENT missing"
    exit 1
}

if ($GCLOUD_FM_HOSTED_ID -eq $null -or $GCLOUD_FM_HOSTED_ID -eq "") {
    Write-Host "ERROR: Required argument GCLOUD_FM_HOSTED_ID missing"
    exit 1
}

if ($GCLOUD_FM_POLL_FREQUENCY -eq $null -or $GCLOUD_FM_POLL_FREQUENCY -eq "") {
    Write-Host "ERROR: Required argument GCLOUD_FM_POLL_FREQUENCY missing"
    exit 1
}

if ($DUPLO_CLUSTER_NAME -eq $null -or $DUPLO_CLUSTER_NAME -eq "") {
    Write-Host "ERROR: Required argument DUPLO_CLUSTER_NAME missing"
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

if ($GCLOUD_RW_API_KEY -eq $null -or $GCLOUD_RW_API_KEY -eq "") {
    Write-Host "ERROR: Required argument GCLOUD_RW_API_KEY missing"
    exit 1
}

if ($GCLOUD_HOSTED_METRICS_URL -eq $null -or $GCLOUD_HOSTED_METRICS_URL -eq "") {
    Write-Host "ERROR: Required argument GCLOUD_HOSTED_METRICS_URL missing"
    exit 1
}

if ($GCLOUD_HOSTED_METRICS_ID -eq $null -or $GCLOUD_HOSTED_METRICS_ID -eq "") {
    Write-Host "ERROR: Required argument GCLOUD_HOSTED_METRICS_ID missing"
    exit 1
}

if ($GCLOUD_HOSTED_LOGS_URL -eq $null -or $GCLOUD_HOSTED_LOGS_URL -eq "") {
    Write-Host "ERROR: Required argument GCLOUD_HOSTED_LOGS_URL missing"
    exit 1
}

if ($GCLOUD_HOSTED_LOGS_ID -eq $null -or $GCLOUD_HOSTED_LOGS_ID -eq "") {
    Write-Host "ERROR: Required argument GCLOUD_HOSTED_LOGS_ID missing"
    exit 1
}

try {
    Write-Host "GCLOUD_FM_URL:" $GCLOUD_FM_URL
    Write-Host "GCLOUD_RW_API_KEY:" $GCLOUD_RW_API_KEY
    Write-Host "GCLOUD_HOSTED_LOGS_URL:" $GCLOUD_HOSTED_LOGS_URL
    Write-Host "DUPLO_CLUSTER_NAME:" $DUPLO_CLUSTER_NAME
    Write-Host "DUPLO_TENANT_NAME:" $DUPLO_TENANT_NAME
    Write-Host "GCLOUD_HOSTED_METRICS_URL:" $GCLOUD_HOSTED_METRICS_URL
    Write-Host "GCLOUD_HOSTED_METRICS_ID:" $GCLOUD_HOSTED_METRICS_ID
    Write-Host "GCLOUD_HOSTED_LOGS_ID:" $GCLOUD_HOSTED_LOGS_ID
    Write-Host "GCLOUD_FM_POLL_FREQUENCY:" $GCLOUD_FM_POLL_FREQUENCY
    Write-Host "GCLOUD_FM_HOSTED_ID:" $GCLOUD_FM_HOSTED_ID
    Write-Host "HOSTNAME:" $HOSTNAME
    Write-Host "ENVIRONMENT:" $ENVIRONMENT

    # --- NEW: Fetch AWS account ID + private IP from IMDSv2 (minimal change) ---
    try {
        $imdsToken = Invoke-RestMethod -Method PUT -Uri "http://169.254.169.254/latest/api/token" -Headers @{"X-aws-ec2-metadata-token-ttl-seconds"="21600"} -TimeoutSec 2
        if ($imdsToken) {
            $imdsHeaders = @{"X-aws-ec2-metadata-token"=$imdsToken}
            $iid = Invoke-RestMethod -Method GET -Uri "http://169.254.169.254/latest/dynamic/instance-identity/document" -Headers $imdsHeaders -TimeoutSec 2
            $AWS_ACCOUNT_ID = $iid.accountId
            $HOST_PRIVATE_IP = $iid.privateIp
            if (-not $HOST_PRIVATE_IP -or $HOST_PRIVATE_IP -eq "") {
                $HOST_PRIVATE_IP = (Invoke-RestMethod -Method GET -Uri "http://169.254.169.254/latest/meta-data/local-ipv4" -Headers $imdsHeaders -TimeoutSec 2)
            }
            Write-Host "AWS_ACCOUNT_ID:" $AWS_ACCOUNT_ID
            Write-Host "HOST_PRIVATE_IP:" $HOST_PRIVATE_IP
        } else {
            Write-Host "WARNING: Could not get IMDS token; account_id/host_ip may be empty."
        }
    } catch {
        Write-Host "WARNING: IMDS query failed; account_id/host_ip may be empty."
    }
    # --- END NEW ---

    Write-Host "Downloading Alloy Windows Installer"
    $DOWNLOAD_URL = "https://github.com/grafana/alloy/releases/download/v1.12.2/alloy-installer-windows-amd64.exe.zip"
    $OUTPUT_ZIP_FILE = ".\alloy-installer-windows-amd64.exe.zip"
    $OUTPUT_FILE = ".\alloy-installer-windows-amd64.exe"
    $WORKING_DIR = Get-Location
    # Robust Download using .NET WebClient
    $webClient = New-Object System.Net.WebClient
    $webClient.DownloadFile($DOWNLOAD_URL, $OUTPUT_ZIP_FILE)
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
    $CONFIG_URI = "https://raw.githubusercontent.com/duplocloud/opentelemetry-release/refs/heads/main/alloy/windows/config-fm.alloy"
    
    Invoke-WebRequest -Uri $CONFIG_URI -Outfile $CONFIG_FILE -ErrorAction Stop
    $content = Get-Content $CONFIG_FILE

    Write-Host "--- Replacing Alloy config params with arg values"
    $content = $content.Replace("{DUPLO_CLUSTER_NAME}", $DUPLO_CLUSTER_NAME)
    $content = $content.Replace("{DUPLO_TENANT_NAME}", $DUPLO_TENANT_NAME)
    $content = $content.Replace("{HOSTNAME}", $HOSTNAME)
    $content = $content.Replace("{ENVIRONMENT}", $ENVIRONMENT)
    $content = $content.Replace("{GCLOUD_HOSTED_METRICS_URL}", $GCLOUD_HOSTED_METRICS_URL)
    $content = $content.Replace("{GCLOUD_HOSTED_METRICS_ID}", $GCLOUD_HOSTED_METRICS_ID)
    $content = $content.Replace("{GCLOUD_HOSTED_LOGS_URL}", $GCLOUD_HOSTED_LOGS_URL)
    $content = $content.Replace("{GCLOUD_HOSTED_LOGS_ID}", $GCLOUD_HOSTED_LOGS_ID)
    $content = $content.Replace("{GCLOUD_FM_URL}", $GCLOUD_FM_URL)
    $content = $content.Replace("{GCLOUD_FM_POLL_FREQUENCY}", $GCLOUD_FM_POLL_FREQUENCY)
    $content = $content.Replace("{GCLOUD_FM_HOSTED_ID}", $GCLOUD_FM_HOSTED_ID)
    $content = $content.Replace("{GCLOUD_RW_API_KEY}", $GCLOUD_RW_API_KEY)
    # --- NEW: inject AWS account + private IP placeholders (minimal change) ---
    $content = $content.Replace("{AWS_ACCOUNT_ID}", $AWS_ACCOUNT_ID)
    $content = $content.Replace("{HOST_PRIVATE_IP}", $HOST_PRIVATE_IP)
    # --- END NEW ---
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
