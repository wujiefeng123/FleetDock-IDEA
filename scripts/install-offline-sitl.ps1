# [UAV-IDE MODIFY] Offline SITL installer for Windows hosts with WSL2/PX4 Ubuntu image import.
param(
  [string]$ResourceDir = "D:\offline-packages",
  [string]$KernelMsi = "wsl_update_x64.msi",
  [string]$UbuntuTar = "px4-ubuntu.tar",
  [string]$QgcInstaller = "QGC-Setup.exe",
  [string]$DistroName = "PX4-Ubuntu",
  [string]$InstallPath = "D:\WSL\PX4-Ubuntu",
  [switch]$SkipQgc
)

$ErrorActionPreference = "Stop"

function Write-Step($Message) {
  Write-Host ""
  Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok($Message) {
  Write-Host "[OK] $Message" -ForegroundColor Green
}

function Write-WarnMsg($Message) {
  Write-Host "[WARN] $Message" -ForegroundColor Yellow
}

function Throw-IfNotAdmin {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = New-Object Security.Principal.WindowsPrincipal($identity)
  if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Please run this script as Administrator."
  }
}

function Get-OptionalFeatureState([string]$FeatureName) {
  $feature = Get-WindowsOptionalFeature -Online -FeatureName $FeatureName
  return $feature.State
}

function Enable-FeatureIfNeeded([string]$FeatureName) {
  $state = Get-OptionalFeatureState -FeatureName $FeatureName
  if ($state -eq "Enabled") {
    Write-Ok "Windows feature $FeatureName is already enabled"
    return $false
  }

  Write-Step "Enabling Windows feature $FeatureName"
  Enable-WindowsOptionalFeature -Online -FeatureName $FeatureName -All -NoRestart | Out-Null
  Write-Ok "Windows feature $FeatureName has been enabled"
  return $true
}

function Test-PendingRestart {
  $paths = @(
    "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending",
    "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired"
  )

  foreach ($path in $paths) {
    if (Test-Path $path) {
      return $true
    }
  }

  return $false
}

function Get-WslStatusOutput {
  try {
    $output = & wsl.exe --status 2>&1
    return @{
      Success = ($LASTEXITCODE -eq 0)
      Output = ($output | Out-String)
    }
  }
  catch {
    return @{
      Success = $false
      Output = $_.Exception.Message
    }
  }
}

function Test-WslKernelReady {
  $status = Get-WslStatusOutput
  if (-not $status.Success) {
    return $false
  }

  return ($status.Output -match "Default Version:\s*2" -or $status.Output -match "WSL version:\s*2")
}

function Install-WslKernel([string]$MsiPath) {
  Write-Step "Installing the offline WSL2 kernel package"
  Start-Process "msiexec.exe" -ArgumentList "/i `"$MsiPath`" /quiet /norestart" -Wait -NoNewWindow
  Write-Ok "Offline WSL2 kernel package installed"
}

function Set-WslDefaultVersion {
  Write-Step "Setting default WSL version to 2"
  & wsl.exe --set-default-version 2 | Out-Host
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to set default WSL version to 2."
  }
  Write-Ok "Default WSL version is now 2"
}

function Get-DistroNames {
  $output = & wsl.exe -l -q 2>$null
  if ($LASTEXITCODE -ne 0) {
    return @()
  }

  return @($output | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

function Test-DistroExists([string]$Name) {
  return (Get-DistroNames) -contains $Name
}

function Import-Distro([string]$Name, [string]$RootPath, [string]$TarPath) {
  Write-Step "Importing the PX4 Ubuntu image into WSL"
  New-Item -ItemType Directory -Force -Path $RootPath | Out-Null
  & wsl.exe --import $Name $RootPath $TarPath --version 2 | Out-Host
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to import the WSL image."
  }
  Write-Ok "WSL image import completed"
}

function Test-DistroBoot([string]$Name) {
  Write-Step "Verifying that the imported distribution can boot"
  & wsl.exe -d $Name -- bash -lc "echo WSL ready && uname -a" | Out-Host
  if ($LASTEXITCODE -ne 0) {
    throw "The imported distribution failed to start."
  }
  Write-Ok "Distribution boot verification passed"
}

function Install-Qgc([string]$InstallerPath) {
  Write-Step "Launching the QGC installer"
  Start-Process -FilePath $InstallerPath -Wait
  Write-Ok "QGC installation flow has been executed"
}

Throw-IfNotAdmin

$kernelPath = Join-Path $ResourceDir $KernelMsi
$ubuntuTarPath = Join-Path $ResourceDir $UbuntuTar
$qgcPath = Join-Path $ResourceDir $QgcInstaller

Write-Step "Checking offline resources"
if (-not (Test-Path $kernelPath)) {
  throw "Offline WSL2 kernel package not found: $kernelPath"
}
if (-not (Test-Path $ubuntuTarPath)) {
  throw "PX4 Ubuntu image not found: $ubuntuTarPath"
}
if ((-not $SkipQgc) -and (-not (Test-Path $qgcPath))) {
  Write-WarnMsg "QGC installer not found: $qgcPath. QGC installation will be skipped."
  $SkipQgc = $true
}
Write-Ok "Offline resource check completed"

$featureChanged = $false
$featureChanged = (Enable-FeatureIfNeeded -FeatureName "Microsoft-Windows-Subsystem-Linux") -or $featureChanged
$featureChanged = (Enable-FeatureIfNeeded -FeatureName "VirtualMachinePlatform") -or $featureChanged

if ($featureChanged -or (Test-PendingRestart)) {
  Write-WarnMsg "A system restart is required before WSL2 installation can continue."
  Write-Host ""
  Write-Host "Please restart Windows now, then run this script again as Administrator." -ForegroundColor Yellow
  exit 0
}

if (-not (Test-WslKernelReady)) {
  Install-WslKernel -MsiPath $kernelPath
}
else {
  Write-Ok "WSL2 kernel is already available"
}

if (Test-PendingRestart) {
  Write-WarnMsg "A system restart is required after the WSL2 kernel installation."
  Write-Host ""
  Write-Host "Please restart Windows now, then run this script again as Administrator." -ForegroundColor Yellow
  exit 0
}

$wslStatus = Get-WslStatusOutput
if (-not $wslStatus.Success) {
  throw "WSL is currently unavailable: $($wslStatus.Output.Trim())"
}

Set-WslDefaultVersion

if (-not (Test-DistroExists -Name $DistroName)) {
  Import-Distro -Name $DistroName -RootPath $InstallPath -TarPath $ubuntuTarPath
}
else {
  Write-Ok "Distribution $DistroName already exists. Skipping import."
}

Test-DistroBoot -Name $DistroName

if (-not $SkipQgc) {
  Install-Qgc -InstallerPath $qgcPath
}
else {
  Write-WarnMsg "QGC installation has been skipped"
}

Write-Step "Offline SITL installation completed"
Write-Host "Validation commands:" -ForegroundColor Green
Write-Host "wsl.exe -l -v" -ForegroundColor White
Write-Host "wsl.exe -d $DistroName -- bash -lc 'pwd && ls'" -ForegroundColor White
