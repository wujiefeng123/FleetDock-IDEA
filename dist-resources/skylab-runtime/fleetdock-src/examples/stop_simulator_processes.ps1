[CmdletBinding()]
param(
    [string]$SimulatorExecutable = $env:AIRSIM_EXE,
    [switch]$ListOnly
)

$ErrorActionPreference = "Stop"

function Resolve-ExistingPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    if ([string]::IsNullOrWhiteSpace($Path)) {
        throw "$Label is empty."
    }

    $expanded = [Environment]::ExpandEnvironmentVariables($Path.Trim().Trim('"'))
    if (-not (Test-Path -LiteralPath $expanded -PathType Leaf)) {
        throw "$Label does not exist: $expanded"
    }

    return [IO.Path]::GetFullPath((Get-Item -LiteralPath $expanded).FullName)
}

function Test-PathUnderRoot {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Candidate,
        [Parameter(Mandatory = $true)]
        [string]$Root
    )

    $separator = [IO.Path]::DirectorySeparatorChar
    $rootPrefix = $Root.TrimEnd([char[]]@('\', '/')) + $separator
    return $Candidate.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)
}

try {
    $simulatorPath = Resolve-ExistingPath `
        -Path $SimulatorExecutable `
        -Label "AIRSIM_EXE"
    if (-not [string]::Equals(
        [IO.Path]::GetExtension($simulatorPath),
        ".exe",
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "AIRSIM_EXE must point to an .exe file: $simulatorPath"
    }

    $processRootPath = [IO.Path]::GetFullPath((Split-Path -Parent $simulatorPath))
    $driveRoot = [IO.Path]::GetPathRoot($processRootPath)
    if ([string]::Equals(
        $processRootPath.TrimEnd([char[]]@('\', '/')),
        $driveRoot.TrimEnd([char[]]@('\', '/')),
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "The directory containing AIRSIM_EXE cannot be a drive root: $processRootPath"
    }

    Write-Host "Simulator process root: $processRootPath"

    $maxPasses = if ($ListOnly) { 1 } else { 5 }
    for ($pass = 1; $pass -le $maxPasses; $pass++) {
        $matchingProcesses = @(
            Get-CimInstance Win32_Process |
                Where-Object {
                    if ([string]::IsNullOrWhiteSpace($_.ExecutablePath)) {
                        return $false
                    }
                    try {
                        $candidatePath = [IO.Path]::GetFullPath($_.ExecutablePath)
                        return (
                            $_.ProcessId -ne $PID -and
                            [string]::Equals(
                                [IO.Path]::GetExtension($candidatePath),
                                ".exe",
                                [StringComparison]::OrdinalIgnoreCase
                            ) -and
                            (Test-PathUnderRoot -Candidate $candidatePath -Root $processRootPath)
                        )
                    }
                    catch {
                        return $false
                    }
                }
        )

        if ($matchingProcesses.Count -eq 0) {
            if ($pass -eq 1) {
                Write-Host "No running simulator processes found under the process root."
            }
            break
        }

        foreach ($process in $matchingProcesses) {
            if ($ListOnly) {
                Write-Host "Would stop PID $($process.ProcessId): $($process.ExecutablePath)"
                continue
            }

            Write-Host "Stopping simulator PID $($process.ProcessId): $($process.ExecutablePath)"
            try {
                Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop
            }
            catch {
                if (Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue) {
                    Write-Warning "Failed to stop simulator PID $($process.ProcessId): $($_.Exception.Message)"
                }
            }
        }

        if (-not $ListOnly) {
            Start-Sleep -Milliseconds 400
        }
    }

    if (-not $ListOnly) {
        $remaining = @(
            Get-CimInstance Win32_Process |
                Where-Object {
                    if (
                        $_.ProcessId -eq $PID -or
                        [string]::IsNullOrWhiteSpace($_.ExecutablePath)
                    ) {
                        return $false
                    }
                    try {
                        return Test-PathUnderRoot `
                            -Candidate ([IO.Path]::GetFullPath($_.ExecutablePath)) `
                            -Root $processRootPath
                    }
                    catch {
                        return $false
                    }
                }
        )
        if ($remaining.Count -gt 0) {
            throw "Simulator processes are still running under the AIRSIM_EXE directory after $maxPasses attempts."
        }
    }
}
catch {
    Write-Error "Failed to clean old simulator processes: $($_.Exception.Message)"
    exit 1
}
