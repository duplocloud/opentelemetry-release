## Installation Guide for Alloy and Windows Exporter on a Standalone Windows Machine

This guide provides step-by-step instructions for installing Alloy and the Windows Exporter on a standalone Windows machine.

### Prerequisites

- **Administrator Access**: Ensure you have administrative privileges on the Windows machine.
- **PowerShell Access**: Ensure PowerShell is installed and accessible.
- **Internet Access**: Required to download installation scripts.

### Installation Steps

#### 1. Install Alloy

1. **Navigate to the Temporary Directory**

   Open a command prompt and navigate to the temp directory:

   ```cmd
   cd "%TEMP%"
   ```

2. **Download and Run the Alloy Installation Script**

   Execute the following PowerShell command to download and run the Alloy installer:

   ```cmd
   powershell -c Invoke-WebRequest "https://raw.githubusercontent.com/duplocloud/opentelemetry-release/refs/heads/main/alloy/windows/script/install-windows-alloy.ps1" -OutFile "install-windows-alloy.ps1" && powershell -executionpolicy Bypass -File ".\install-windows-alloy.ps1" -DUPLO_LOGGING_PATHS "[{\"__path__\" = \"C:\\var\\log\\*.log\", \"__address__\" = \"localhost\", \"job\" = \"integrations/iis\" or \"integrations/windows_exporter\", \"site\" = \"<your-site1-name>\"}]" -DUPLO_METRICS_URL "https://<metrics_url>/api/v1/push" -DUPLO_LOGGING_URL "https://<logs_url>/loki/api/v1/push" -DUPLO_TENANT_NAME "tenant name" -HOSTNAME "hostname"
   ```

   - **Parameters**:
     - `DUPLO_LOGGING_PATHS`: Specifies the paths for logging.
     - `DUPLO_METRICS_URL`: URL to send metrics.
     - `DUPLO_LOGGING_URL`: URL to push logs.
     - `DUPLO_TENANT_NAME`: duplo tenant name.
     - `HOSTNAME`: windows hostname.

#### 2. Install Windows Exporter

1. **Download and Run the Windows Exporter Installation Script**

   Execute the following PowerShell command to download and run the Windows Exporter installer:

   ```cmd
   powershell -c Invoke-WebRequest "https://raw.githubusercontent.com/duplocloud/opentelemetry-release/refs/heads/main/alloy/windows/script/install-windows-exporter.ps1" -OutFile "install-windows-exporter.ps1" && powershell -executionpolicy Bypass -File ".\install-windows-exporter.ps1"
   ```

### Alternative PowerShell Commands

For those more comfortable with PowerShell scripts, the following commands achieve the same tasks:

- **Windows Exporter Installation:**

  ```powershell
  Set-Location -Path $env:TEMP;
  Invoke-WebRequest -Uri "https://raw.githubusercontent.com/duplocloud/opentelemetry-release/refs/heads/main/alloy/windows/script/install-windows-exporter.ps1" -OutFile "install-windows-exporter.ps1";
  powershell -ExecutionPolicy Bypass -File "./install-windows-exporter.ps1";
  ```

- **Alloy Installation:**

  ```powershell
  Set-Location -Path $env:TEMP;
  Invoke-WebRequest -Uri "https://raw.githubusercontent.com/duplocloud/opentelemetry-release/refs/heads/main/alloy/windows/script/install-windows-alloy.ps1" -OutFile "install-windows-alloy.ps1";
  $duploLoggingPaths = '[{\"__path__\" = \"C:\\var\\log\\*.log\"}]';
  powershell -ExecutionPolicy Bypass -File "./install-windows-alloy.ps1" -DUPLO_LOGGING_PATHS $duploLoggingPaths -DUPLO_METRICS_URL "https://<metrics_url>/api/v1/push" -DUPLO_LOGGING_URL "https://<logs_url>/loki/api/v1/push" -DUPLO_TENANT_NAME "tenant name" -HOSTNAME "hostname";
  ```

### Troubleshooting

- **Script Execution Policy**: If you encounter issues running scripts, ensure that the execution policy allows script execution. Use `Set-ExecutionPolicy` to adjust as needed.
  
- **Internet Connection**: Ensure the machine has an active internet connection to download scripts.

- **Administrator Rights**: Run PowerShell as an administrator to avoid permission-related issues.

This guide ensures that Alloy and Windows Exporter are installed and configured correctly on your standalone Windows machine, ready for operation.