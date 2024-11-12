# Define the URL for the Windows Exporter executable
$windowsExporterExeUrl = "https://github.com/prometheus-community/windows_exporter/releases/download/v0.28.1/windows_exporter-0.28.1-amd64.exe"

# Specify the download and rename paths
$destinationFolder = "C:\Program Files\Windows Exporter"
$originalExeFilePath = "$destinationFolder\windows_exporter-0.28.1-amd64.exe"
$renamedExeFilePath = "$destinationFolder\windows_exporter.exe"

# Define the service name and display name
$serviceName = "WindowsExporter"
$serviceDisplayName = "Windows Exporter Service"
$serviceDescription = "Prometheus Windows Exporter Service"
$arguments = "--collectors.enabled=cpu,cs,logical_disk,net,os,service,system,time,diskdrive"

# Create the directory if it doesn't exist
if (-Not (Test-Path -Path $destinationFolder)) {
    New-Item -Path $destinationFolder -ItemType Directory | Out-Null
}

# Download the executable
Invoke-WebRequest -Uri $windowsExporterExeUrl -OutFile $originalExeFilePath

# Rename the file
Rename-Item -Path $originalExeFilePath -NewName $renamedExeFilePath

Write-Host "Windows Exporter downloaded and renamed to $renamedExeFilePath"

# Check if the service exists and retrieve its status
$service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue

if ($null -ne $service) {
    if ($service.Status -eq 'Running') {
        Write-Host "$serviceDisplayName is already running."
    } else {
        Start-Service -Name $serviceName
        Write-Host "Service $serviceDisplayName was not running and has been started."
    }
} else {
    # Create the service if it doesn't exist
    New-Service -Name $serviceName `
        -BinaryPathName "`"$renamedExeFilePath`" $arguments" `
        -DisplayName $serviceDisplayName `
        -Description $serviceDescription `
        -StartupType Automatic

    # Start the service
    Start-Service -Name $serviceName
    Write-Host "Service $serviceDisplayName has been created and started."
}

# Final check and report service status
$serviceStatus = Get-Service -Name $serviceName
Write-Host "Service '$serviceDisplayName' is currently $($serviceStatus.Status)."
