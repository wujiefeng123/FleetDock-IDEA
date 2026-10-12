@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion

set "CONDA_BAT=D:\ProgramData\anaconda3\condabin\conda.bat"
set "CONDA_NAME=fleetdock"
set "SCRIPT_DIR=%~dp0"
set "WSL_SCRIPT_DIR=/mnt/e/git/github/FleetDock/examples"
set "WSL_DISTRO=Ubuntu-24.04"
set "AIRSIM_EXE=F:\runtime\ProjectAirSim\Neighborhood-Windows-UE5.2-PAS_v0.2.0\AirSimNH.exe"
set "PROJECTAIRSIM_SCENE_CONFIG_PATH="
rem 留空时使用 projectairsim\scenes\scene_px4_neighborhood.jsonc；填写绝对路径时使用指定场景。
set "AIRSIM_STARTUP_WAIT_S=5"
set "PROJECTAIRSIM_EXAMPLES_DIR=E:\git\github\ProjectAirSim\client\python\example_user_scripts"
set "PROJECTAIRSIM_SRC_DIR=E:\git\github\ProjectAirSim\client\python\projectairsim\src"
if not defined PROJECTAIRSIM_ROBOT_PROFILE set "PROJECTAIRSIM_ROBOT_PROFILE=basic"
rem PROJECTAIRSIM_SENSOR_PROFILES combines sensors; MULTI_PROJECTAIRSIM_SENSOR_PROFILES overrides each drone.
rem Default home is near Chongqing Rail Transit University Town station; altitude is approximate AMSL meters.
set "PROJECTAIRSIM_HOME_LATITUDE_DEG=29.61002"
set "PROJECTAIRSIM_HOME_LONGITUDE_DEG=106.30514"
set "PROJECTAIRSIM_HOME_ALTITUDE_M=293.0"
set "PX4_ROOT_WSL=/mnt/e/git/github/PX4-Autopilot"
set "PX4_BUILD_DIR_WSL=/mnt/e/git/github/PX4-Autopilot/build/px4_sitl_default"
set "PX4_READY_TIMEOUT_S=180"
set "PX4_INITIAL_HEADING_DEG=0"
set "PX4_SIM_HOST_ADDR="
set "PX4_WINDOWS_HOST_IP="
set "PX4_MAVLINK_RATE=4000000"
set "PX4_MPC_XY_VEL_MAX=20"

set "MULTI_DRONE_COUNT=3"
set "MULTI_PROJECTAIRSIM_SCENE_NAME=scene_fleetdock_px4_multi.generated.jsonc"
if not defined MULTI_PROJECTAIRSIM_SENSOR_DRONES set "MULTI_PROJECTAIRSIM_SENSOR_DRONES=Drone1"
set "MULTI_PROJECTAIRSIM_SPAWN_SPACING_M=10.0"
set "MULTI_PX4_SIM_MODEL=none_iris"
set "MULTI_PX4_SOCKET_PREFIX=/tmp/px4-sock-"
set "MULTI_MAVLINK_LOCAL_PORT_BASE=14660"
set "MULTI_MAVLINK_REMOTE_PORT_BASE=14555"
set "MULTI_PX4_DEFAULT_GCS_PORT_BASE=18570"
set "MULTI_DRONE_NAME_PREFIX=Drone"

echo load env.bat
if exist "%SCRIPT_DIR%env.bat" call "%SCRIPT_DIR%env.bat"

if not exist "%CONDA_BAT%" (
    echo Conda launcher not found: %CONDA_BAT%
    echo Set CONDA_BAT in examples\env.bat to the local conda.bat path.
    exit /b 1
)
echo Conda launcher: %CONDA_BAT%

set "DETECTED_PX4_WINDOWS_HOST_IP="
for /l %%A in (1,1,10) do (
    if not defined DETECTED_PX4_WINDOWS_HOST_IP (
        for /f "tokens=3" %%I in ('wsl -d %WSL_DISTRO% -- ip route show default') do set "DETECTED_PX4_WINDOWS_HOST_IP=%%I"
        if not defined DETECTED_PX4_WINDOWS_HOST_IP powershell -NoProfile -Command "Start-Sleep -Seconds 1"
    )
)
if not defined DETECTED_PX4_WINDOWS_HOST_IP if defined PX4_WINDOWS_HOST_IP (
    echo 未能从 WSL 检测 Windows 主机 IP，改用配置值 %PX4_WINDOWS_HOST_IP%。
    set "DETECTED_PX4_WINDOWS_HOST_IP=%PX4_WINDOWS_HOST_IP%"
)
if not defined DETECTED_PX4_WINDOWS_HOST_IP (
    echo 连续 10 次未能从 WSL 检测 Windows 主机 IP。
    echo 请在 examples\env.bat 中设置 PX4_WINDOWS_HOST_IP，或检查 WSL 网络。
    exit /b 1
)
if defined PX4_WINDOWS_HOST_IP if not "%PX4_WINDOWS_HOST_IP%"=="%DETECTED_PX4_WINDOWS_HOST_IP%" (
    echo Configured PX4_WINDOWS_HOST_IP %PX4_WINDOWS_HOST_IP% differs from detected %DETECTED_PX4_WINDOWS_HOST_IP%; use detected value.
)
set "PX4_WINDOWS_HOST_IP=%DETECTED_PX4_WINDOWS_HOST_IP%"
if defined PX4_SIM_HOST_ADDR if not "%PX4_SIM_HOST_ADDR%"=="%DETECTED_PX4_WINDOWS_HOST_IP%" (
    echo Configured PX4_SIM_HOST_ADDR %PX4_SIM_HOST_ADDR% differs from detected %DETECTED_PX4_WINDOWS_HOST_IP%; use detected value.
)
set "PX4_SIM_HOST_ADDR=%PX4_WINDOWS_HOST_IP%"
echo WSL Windows host IP: %PX4_WINDOWS_HOST_IP%

set "MULTI_DRONE_NAMES="
set "MULTI_ENDPOINTS="
for /l %%N in (1,1,%MULTI_DRONE_COUNT%) do (
    set /a "DRONE_INDEX=%%N-1"
    set /a "DRONE_REMOTE_PORT=%MULTI_MAVLINK_REMOTE_PORT_BASE%+!DRONE_INDEX!"
    if defined MULTI_DRONE_NAMES (
        set "MULTI_DRONE_NAMES=!MULTI_DRONE_NAMES!,%MULTI_DRONE_NAME_PREFIX%%%N"
        set "MULTI_ENDPOINTS=!MULTI_ENDPOINTS!, %MULTI_DRONE_NAME_PREFIX%%%N udpin://0.0.0.0:!DRONE_REMOTE_PORT!"
    ) else (
        set "MULTI_DRONE_NAMES=%MULTI_DRONE_NAME_PREFIX%%%N"
        set "MULTI_ENDPOINTS=%MULTI_DRONE_NAME_PREFIX%%%N udpin://0.0.0.0:!DRONE_REMOTE_PORT!"
    )
)
echo Drones: %MULTI_DRONE_NAMES%

if not exist "%AIRSIM_EXE%" (
    echo AirSim executable not found: %AIRSIM_EXE%
    exit /b 1
)

echo run airsim
cmd /c taskkill /im mavsdk_server.exe /f
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%stop_simulator_processes.ps1"
if not "%errorlevel%"=="0" (
    echo failed to stop old simulator processes
    exit /b 1
)
start "" "%AIRSIM_EXE%" %AIRSIM_ARGS%
powershell -NoProfile -Command "Start-Sleep -Seconds %AIRSIM_STARTUP_WAIT_S%"

echo build px4 sitl
wsl -d %WSL_DISTRO% -- bash -lc "cd '%PX4_ROOT_WSL%' ; make px4_sitl_default"
if not "%errorlevel%"=="0" (
    echo failed to build px4 sitl
    exit /b 1
)

echo stop old px4 instances
wsl -d %WSL_DISTRO% -- bash -lc "pkill -TERM -x px4 || true; for attempt in {1..15}; do pgrep -x px4 >/dev/null || break; sleep 1; done; if pgrep -x px4 >/dev/null; then echo 'Old PX4 instances still running after TERM, force kill.'; pkill -KILL -x px4 || true; for attempt in {1..10}; do pgrep -x px4 >/dev/null || break; sleep 1; done; fi; if pgrep -x px4 >/dev/null; then echo 'Warning: old PX4 process entries still exist after KILL; continue startup.'; fi"
if not "%errorlevel%"=="0" (
    echo failed to stop old px4 instances
    exit /b 1
)

echo clean stale px4 sockets
for /l %%N in (1,1,%MULTI_DRONE_COUNT%) do (
    set /a "DRONE_INDEX=%%N-1"
    set "PX4_SOCKET=%MULTI_PX4_SOCKET_PREFIX%!DRONE_INDEX!"
    wsl -d %WSL_DISTRO% -- bash -lc "rm -f -- '!PX4_SOCKET!'"
    if not "!errorlevel!"=="0" (
        echo failed to clean stale px4 socket !PX4_SOCKET!
        exit /b 1
    )
)

echo run %MULTI_DRONE_COUNT% px4 instances
for /l %%N in (1,1,%MULTI_DRONE_COUNT%) do (
    set /a "DRONE_INDEX=%%N-1"
    set "PX4_SOCKET=%MULTI_PX4_SOCKET_PREFIX%!DRONE_INDEX!"
    start "PX4 SITL %MULTI_DRONE_NAME_PREFIX%%%N" cmd /k wsl -d %WSL_DISTRO% -- bash -lc "cd '%PX4_BUILD_DIR_WSL%' ; PX4_SIM_HOST_ADDR='%PX4_SIM_HOST_ADDR%' PX4_SIM_MODEL='%MULTI_PX4_SIM_MODEL%' ./bin/px4 -i !DRONE_INDEX!"
)

echo wait for all px4 sockets before loading projectairsim multi-drone scene
for /l %%N in (1,1,%MULTI_DRONE_COUNT%) do (
    set /a "DRONE_INDEX=%%N-1"
    set "PX4_SOCKET=%MULTI_PX4_SOCKET_PREFIX%!DRONE_INDEX!"
    set "PX4_INSTANCE=!DRONE_INDEX!"
    echo waiting %MULTI_DRONE_NAME_PREFIX%%%N socket !PX4_SOCKET!
    wsl -d %WSL_DISTRO% -- bash -lc "CONDA_NAME='%CONDA_NAME%' PX4_SOCKET='!PX4_SOCKET!' PX4_INSTANCE='!PX4_INSTANCE!' PX4_BUILD_DIR='%PX4_BUILD_DIR_WSL%' PX4_READY_TIMEOUT_S='%PX4_READY_TIMEOUT_S%' PX4_SOCKET_ONLY=1 bash '%WSL_SCRIPT_DIR%/wait_px4_ready.sh'"
    if not "!errorlevel!"=="0" (
        echo failed to wait px4 instance !PX4_INSTANCE!
        exit /b 1
    )
)

echo configure unique MAVLink system IDs
for /l %%N in (1,1,%MULTI_DRONE_COUNT%) do (
    set /a "DRONE_INDEX=%%N-1"
    set /a "PX4_SYSTEM_ID=%%N"
    set "PX4_SOCKET=%MULTI_PX4_SOCKET_PREFIX%!DRONE_INDEX!"
    echo %MULTI_DRONE_NAME_PREFIX%%%N: MAV_SYS_ID=!PX4_SYSTEM_ID!
    wsl -d %WSL_DISTRO% -- bash -lc "CONDA_NAME='%CONDA_NAME%' PX4_SOCKET='!PX4_SOCKET!' PX4_INSTANCE='!DRONE_INDEX!' PX4_BUILD_DIR='%PX4_BUILD_DIR_WSL%' PX4_READY_TIMEOUT_S='%PX4_READY_TIMEOUT_S%' bash '%WSL_SCRIPT_DIR%/wait_px4_ready.sh'"
    if not "!errorlevel!"=="0" (
        echo failed to wait for PX4 parameter server !DRONE_INDEX!
        exit /b 1
    )
    wsl -d %WSL_DISTRO% -- bash -lc "cd '%PX4_BUILD_DIR_WSL%' && for attempt in {1..10}; do ./bin/px4-param --instance '!DRONE_INDEX!' set MAV_SYS_ID '!PX4_SYSTEM_ID!' && exit 0; sleep 1; done; exit 1"
    if not "!errorlevel!"=="0" (
        echo failed to configure MAV_SYS_ID for PX4 instance !DRONE_INDEX!
        exit /b 1
    )
)

echo prepare projectairsim scene and drones
set "PROJECTAIRSIM_SCENE_NAME=%MULTI_PROJECTAIRSIM_SCENE_NAME%"
set "PROJECTAIRSIM_DRONE_NAMES=%MULTI_DRONE_NAMES%"
call "%CONDA_BAT%" run --no-capture-output -n "%CONDA_NAME%" python "%SCRIPT_DIR%prepare_projectairsim_drone.py"
if not "%errorlevel%"=="0" (
    echo failed to prepare projectairsim scene and drones
    exit /b 1
)

echo wait for px4 initialization and start mavlink output
for /l %%N in (1,1,%MULTI_DRONE_COUNT%) do (
    set /a "DRONE_INDEX=%%N-1"
    set /a "PX4_MAVLINK_LOCAL_PORT=%MULTI_MAVLINK_LOCAL_PORT_BASE%+!DRONE_INDEX!"
    set /a "PX4_MAVLINK_REMOTE_PORT=%MULTI_MAVLINK_REMOTE_PORT_BASE%+!DRONE_INDEX!"
    set /a "PX4_MAVLINK_REPLACE_LOCAL_PORT=%MULTI_PX4_DEFAULT_GCS_PORT_BASE%+!DRONE_INDEX!"
    set "PX4_SOCKET=%MULTI_PX4_SOCKET_PREFIX%!DRONE_INDEX!"
    set "PX4_INSTANCE=!DRONE_INDEX!"
    echo %MULTI_DRONE_NAME_PREFIX%%%N: instance !PX4_INSTANCE!, socket !PX4_SOCKET!, MAVSDK endpoint udpin://0.0.0.0:!PX4_MAVLINK_REMOTE_PORT!
    call "%SCRIPT_DIR%start_px4_mavlink.bat"
    if not "!errorlevel!"=="0" (
        echo failed to start px4 mavlink output for %MULTI_DRONE_NAME_PREFIX%%%N
        exit /b 1
    )
)

echo open airsim dev shell
start "FleetDock %CONDA_NAME% multi shell" cmd /k "cd /d ^"%SCRIPT_DIR%^" && set CONDA_NAME=%CONDA_NAME% && set PX4_BUILD_DIR_WSL=%PX4_BUILD_DIR_WSL% && call ^"%CONDA_BAT%^" activate ^"%CONDA_NAME%^" && echo FleetDock %CONDA_NAME% multi shell is ready. && echo Endpoints: %MULTI_ENDPOINTS%"

exit /b 0
