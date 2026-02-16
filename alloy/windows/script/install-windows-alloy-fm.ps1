# Script to install Alloy for Windows
param (
    $DUPLO_CLUSTER_NAME,
    $DUPLO_TENANT_NAME,
    $HOSTNAME,
    $ENVIRONMENT,
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
if ($null -eq $GCLOUD_FM_URL -or $GCLOUD_FM_URL -eq "") {
    Write-Host "ERROR: Required argument GCLOUD_FM_URL missing"
    exit 1
}

if ($null -eq $ENVIRONMENT -or $ENVIRONMENT -eq "") {
    Write-Host "ERROR: Required argument ENVIRONMENT missing"
    exit 1
}

if ($null -eq $GCLOUD_FM_HOSTED_ID -or $GCLOUD_FM_HOSTED_ID -eq "") {
    Write-Host "ERROR: Required argument GCLOUD_FM_HOSTED_ID missing"
    exit 1
}

if ($null -eq $GCLOUD_FM_POLL_FREQUENCY -or $GCLOUD_FM_POLL_FREQUENCY -eq "") {
    Write-Host "ERROR: Required argument GCLOUD_FM_POLL_FREQUENCY missing"
    exit 1
}

if ($null -eq $DUPLO_CLUSTER_NAME -or $DUPLO_CLUSTER_NAME -eq "") {
    Write-Host "ERROR: Required argument DUPLO_CLUSTER_NAME missing"
    exit 1
}

if ($null -eq $DUPLO_TENANT_NAME -or $DUPLO_TENANT_NAME -eq "") {
    Write-Host "ERROR: Required argument DUPLO_TENANT_NAME missing"
    exit 1
}

if ($null -eq $HOSTNAME -or $HOSTNAME -eq "") {
    Write-Host "ERROR: Required argument HOSTNAME missing"
    exit 1
}

if ($null -eq $GCLOUD_RW_API_KEY -or $GCLOUD_RW_API_KEY -eq "") {
    Write-Host "ERROR: Required argument GCLOUD_RW_API_KEY missing"
    exit 1
}

$WORKING_DIR = Get-Location

try {
    $INSTANCE_ID = (Invoke-WebRequest -usebasicparsing http://169.254.169.254/latest/meta-data/instance-id).Content

    $callerInfo = (aws sts get-caller-identity) | ConvertFrom-Json
    $AWS_ACCOUNT = $callerInfo.Account

    $adapterName = (get-netadapter).Name
    $interfaceInfo = (Get-NetIPAddress -InterfaceAlias $adapterName -AddressFamily IPv4)
    $PRIVATE_IP_ADDRESS = $interfaceInfo.IPAddress

    Write-Host "GCLOUD_FM_URL:" $GCLOUD_FM_URL
    Write-Host "GCLOUD_RW_API_KEY:" $GCLOUD_RW_API_KEY
    Write-Host "DUPLO_CLUSTER_NAME:" $DUPLO_CLUSTER_NAME
    Write-Host "DUPLO_TENANT_NAME:" $DUPLO_TENANT_NAME
    Write-Host "GCLOUD_FM_POLL_FREQUENCY:" $GCLOUD_FM_POLL_FREQUENCY
    Write-Host "GCLOUD_FM_HOSTED_ID:" $GCLOUD_FM_HOSTED_ID
    Write-Host "HOSTNAME:" $HOSTNAME
    Write-Host "ENVIRONMENT:" $ENVIRONMENT
    Write-Host "PRIVATE_IP_ADDRESS:" $PRIVATE_IP_ADDRESS
    Write-Host "INSTANCE_ID:" $INSTANCE_ID
    Write-Host "AWS_ACCOUNT:" $AWS_ACCOUNT

    Write-Host "Downloading Alloy Windows Installer"
    $TEMP_DIR = "C:\temp"
    $DOWNLOAD_URL = "https://github.com/grafana/alloy/releases/download/v1.12.2/alloy-installer-windows-amd64.exe.zip"
    $OUTPUT_ZIP_FILE = "$TEMP_DIR\alloy-installer-windows-amd64.exe.zip"
    $OUTPUT_FILE = "$TEMP_DIR\alloy-installer-windows-amd64.exe"

    Write-Host "Checking for local installer zip file"
    $ZIP_FILE_EXISTS = Test-Path -Path $OUTPUT_ZIP_FILE
    Write-Host "Checking for local installer file"
    $INSTALLER_FILE_EXISTS = Test-Path -Path $OUTPUT_FILE

    # if output file exists or output zip file exists, do not download
    if ($ZIP_FILE_EXISTS -or $INSTALLER_FILE_EXISTS) {
        $DOWNLOAD_NOT_NEEDED = $true
    }
    else {
        $DOWNLOAD_NOT_NEEDED = $false
    }

    if ($DOWNLOAD_NOT_NEEDED -eq $false) {
        Write-Host "Downloading Alloy Windows Installer"
        # Robust Download using .NET WebClient
        $webClient = New-Object System.Net.WebClient
        $webClient.DownloadFile($DOWNLOAD_URL, $OUTPUT_ZIP_FILE)
    }

    # if output file exists, do not expand
    if ($INSTALLER_FILE_EXISTS -eq $false) {
        Expand-Archive -LiteralPath $OUTPUT_ZIP_FILE -DestinationPath $WORKING_DIR.path -force -ErrorAction Stop
    }
}
catch {
    Write-Host "ERROR: Failed to extract Alloy installer for Windows"
    Write-Error $_.Exception
    exit 1
}

# Install Alloy in silent mode
Write-Host "Installing Alloy for Windows"
$INSTALL_STDOUT_PATH = "$TEMP_DIR\install-stdout.txt"
$INSTALL_STDERR_PATH = "$TEMP_DIR\install-stderr.txt"
$INSTALL_PROC = Start-Process "$OUTPUT_FILE" -ArgumentList "/S", "/DISABLEREPORTING=yes", "/STABILITY=experimental" -Wait -PassThru -RedirectStandardOutput $INSTALL_STDOUT_PATH -RedirectStandardError $INSTALL_STDERR_PATH
if ($INSTALL_PROC.ExitCode -ne 0) {
    Write-Host "ERROR: Failed to install Alloy"
    Write-Host "Alloy Install STDOUT: $(Get-Content $INSTALL_STDOUT_PATH)"
    Write-Host "Alloy Install STDERR: $(Get-Content $INSTALL_STDERR_PATH)"
    exit $INSTALL_PROC.ExitCode
}

try {
    $CONFIG_FILE = "$TEMP_DIR\config.alloy"

    Write-Host "Checking for alloy config file on disk"
    $CONFIG_FILE_EXISTS = Test-Path -Path $CONFIG_FILE

    if ($CONFIG_FILE_EXISTS -eq $false) {
        Write-Host "--- Retrieving Alloy config"
        $CONFIG_URI = "https://raw.githubusercontent.com/duplocloud/opentelemetry-release/refs/heads/main/alloy/windows/config-fm.alloy"
        
        Invoke-WebRequest -Uri $CONFIG_URI -Outfile $CONFIG_FILE -ErrorAction Stop
    }

    $content = Get-Content $CONFIG_FILE

    Write-Host "--- Replacing Alloy config params with arg values"
    $content = $content.Replace("{DUPLO_CLUSTER_NAME}", $DUPLO_CLUSTER_NAME)
    $content = $content.Replace("{DUPLO_TENANT_NAME}", $DUPLO_TENANT_NAME)
    $content = $content.Replace("{HOSTNAME}", $HOSTNAME)
    $content = $content.Replace("{ENVIRONMENT}", $ENVIRONMENT)
    $content = $content.Replace("{GCLOUD_FM_URL}", $GCLOUD_FM_URL)
    $content = $content.Replace("{GCLOUD_FM_POLL_FREQUENCY}", $GCLOUD_FM_POLL_FREQUENCY)
    $content = $content.Replace("{GCLOUD_FM_HOSTED_ID}", $GCLOUD_FM_HOSTED_ID)
    $content = $content.Replace("{PRIVATE_IP_ADDRESS}", $PRIVATE_IP_ADDRESS)
    $content = $content.Replace("{INSTANCE_ID}", $INSTANCE_ID)
    $content = $content.Replace("{AWS_ACCOUNT}", $AWS_ACCOUNT)
    $content | Set-Content $CONFIG_FILE

    Write-Host "Creating Alloy system environment variable"
    [Environment]::SetEnvironmentVariable("GCLOUD_RW_API_KEY", $GCLOUD_RW_API_KEY, "Machine")

    $DEST_DIR = "C:\Program Files\GrafanaLabs\Alloy"
    Write-Host "--- Moving finalized Alloy config to $DEST_DIR\config.alloy"
    Move-Item $CONFIG_FILE "$DEST_DIR\config.alloy" -force -ErrorAction Stop
}
catch {
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
