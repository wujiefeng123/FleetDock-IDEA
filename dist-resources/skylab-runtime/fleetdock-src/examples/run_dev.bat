@echo off
chcp 65001 >nul
set "CONDA_BAT=D:\ProgramData\anaconda3\condabin\conda.bat"
set "CONDA_NAME=fleetdock"
set "SCRIPT_DIR=%~dp0"
set "WSL_SCRIPT_DIR=/mnt/e/git/github/FleetDock/examples"
set "WSL_DISTRO=Ubuntu-24.04"
set "AIRSIM_EXE=F:\runtime\ProjectAirSim\Neighborhood-Windows-UE5.2-PAS_v0.2.0\AirSimNH.exe"
set "AIRSIM_STARTUP_WAIT_S=5"
set "PROJECTAIRSIM_EXAMPLES_DIR=E:\git\github\ProjectAirSim\client\python\example_user_scripts"
set "PROJECTAIRSIM_SRC_DIR=E:\git\github\ProjectAirSim\client\python\projectairsim\src"
if not defined PROJECTAIRSIM_SCENE_NAME set "PROJECTAIRSIM_SCENE_NAME=scene_fleetdock_px4_single.generated.jsonc"
if not defined PROJECTAIRSIM_ROBOT_PROFILE set "PROJECTAIRSIM_ROBOT_PROFILE=basic"
rem PROJECTAIRSIM_SENSOR_PROFILES accepts comma-separated profiles and overrides PROJECTAIRSIM_ROBOT_PROFILE.
rem Default home is near Chongqing Rail Transit University Town station; altitude is approximate AMSL meters.
set "PROJECTAIRSIM_HOME_LATITUDE_DEG=29.61002"
set "PROJECTAIRSIM_HOME_LONGITUDE_DEG=106.30514"
set "PROJECTAIRSIM_HOME_ALTITUDE_M=293.0"
set "PX4_ROOT_WSL=/mnt/e/git/github/PX4-Autopilot"
set "PX4_BUILD_DIR_WSL=/mnt/e/git/github/PX4-Autopilot/build/px4_sitl_default"
set "PX4_SOCKET=/tmp/px4-sock-0"
set "PX4_INSTANCE=0"
set "PX4_READY_TIMEOUT_S=180"
set "PX4_INITIAL_HEADING_DEG=0"
set "PX4_SIM_HOST_ADDR="
set "PX4_WINDOWS_HOST_IP="
set "PX4_MAVLINK_LOCAL_PORT=14660"
set "PX4_MAVLINK_REMOTE_PORT=14555"
set "PX4_MAVLINK_RATE=4000000"
set "PX4_MPC_XY_VEL_MAX=20"

echo load env.bat
if exist "%SCRIPT_DIR%env.bat" call "%SCRIPT_DIR%env.bat"

if not exist "%CONDA_BAT%" (
    echo Conda launcher not found: %CONDA_BAT%
    echo Set CONDA_BAT in examples\env.bat to the local conda.bat path.
    exit /b 1
)
echo Conda launcher: %CONDA_BAT%

for /f "tokens=3" %%I in ('wsl -d %WSL_DISTRO% -- ip route show default') do set "DETECTED_PX4_WINDOWS_HOST_IP=%%I"
if not defined DETECTED_PX4_WINDOWS_HOST_IP (
    echo Failed to detect the Windows host IP from WSL.
    exit /b 1
)
if defined PX4_WINDOWS_HOST_IP if not "%PX4_WINDOWS_HOST_IP%"=="%DETECTED_PX4_WINDOWS_HOST_IP%" (
    echo Configured PX4_WINDOWS_HOST_IP %PX4_WINDOWS_HOST_IP% differs from detected %DETECTED_PX4_WINDOWS_HOST_IP%; use detected value.
)
set "PX4_WINDOWS_HOST_IP=%DETECTED_PX4_WINDOWS_HOST_IP%"
if defined PX4_SIM_HOST_ADDR if not "%PX4_SIM_HOST_ADDR%"=="%DETECTED_PX4_WINDOWS_HOST_IP%" (
    echo Configured PX4_SIM_HOST_ADDR %PX4_SIM_HOST_ADDR% differs from detected %DETECTED_PX4_WINDOWS_HOST_IP%; use detected value.
)
if not defined PX4_SIM_HOST_ADDR set "PX4_SIM_HOST_ADDR=%PX4_WINDOWS_HOST_IP%"
set "PX4_SIM_HOST_ADDR=%PX4_WINDOWS_HOST_IP%"
echo WSL Windows host IP: %PX4_WINDOWS_HOST_IP%

if not exist "%AIRSIM_EXE%" (
    echo AirSim executable not found: %AIRSIM_EXE%
    exit /b 1
)

echo run airsim
cmd /c taskkill /im AirSimNH.exe /f
cmd /c taskkill /im AirSimNH-Win64-Shipping.exe /f
start "" "%AIRSIM_EXE%" %AIRSIM_ARGS%
powershell -NoProfile -Command "Start-Sleep -Seconds %AIRSIM_STARTUP_WAIT_S%"


echo run px4
wsl -d %WSL_DISTRO% -- bash -lc "pkill -f 'px4_sitl none_iris'"
wsl -d %WSL_DISTRO% -- bash -lc "pkill -f '%PX4_BUILD_DIR_WSL%/bin/px4'"
start "PX4 SITL" cmd /k wsl -d %WSL_DISTRO% -- bash -lc "cd '%PX4_ROOT_WSL%' ; export PX4_SIM_HOST_ADDR='%PX4_SIM_HOST_ADDR%' ; make px4_sitl none_iris"

echo prepare projectairsim scene and drone after px4 socket is ready
wsl -d %WSL_DISTRO% -- bash -lc "CONDA_NAME='%CONDA_NAME%' PX4_SOCKET='%PX4_SOCKET%' PX4_INSTANCE='%PX4_INSTANCE%' PX4_BUILD_DIR='%PX4_BUILD_DIR_WSL%' PX4_READY_TIMEOUT_S='%PX4_READY_TIMEOUT_S%' PX4_SOCKET_ONLY=1 bash '%WSL_SCRIPT_DIR%/wait_px4_ready.sh'"
if not "%errorlevel%"=="0" (
    echo failed to wait for px4 socket before preparing projectairsim scene and drone
    exit /b 1
)
call "%CONDA_BAT%" run --no-capture-output -n "%CONDA_NAME%" python "%SCRIPT_DIR%prepare_projectairsim_drone.py"
if not "%errorlevel%"=="0" (
    echo failed to prepare projectairsim scene and drone
    exit /b 1
)

echo wait for px4 initialization and start mavlink output
call "%SCRIPT_DIR%start_px4_mavlink.bat"
if not "%errorlevel%"=="0" (
    echo failed to start px4 mavlink output
    exit /b 1
)

echo open airsim dev shell
start "FleetDock %CONDA_NAME% shell" cmd /k "cd /d ^"%SCRIPT_DIR%^" && set CONDA_NAME=%CONDA_NAME% && call ^"%CONDA_BAT%^" activate ^"%CONDA_NAME%^" && echo FleetDock %CONDA_NAME% shell is ready. && echo Run: python mavlink_random_route_rtl.py"

exit /b 0
