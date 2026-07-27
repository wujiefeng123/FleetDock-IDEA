@echo off
setlocal

for %%V in (WSL_DISTRO WSL_SCRIPT_DIR PX4_BUILD_DIR_WSL PX4_SOCKET PX4_INSTANCE PX4_READY_TIMEOUT_S PX4_WINDOWS_HOST_IP PX4_MAVLINK_LOCAL_PORT PX4_MAVLINK_REMOTE_PORT PX4_MAVLINK_RATE PX4_MPC_XY_VEL_MAX) do (
    if not defined %%V (
        echo Missing environment variable: %%V
        exit /b 1
    )
)

echo Waiting for PX4 commander to become ready...
wsl -d %WSL_DISTRO% -- bash -lc "CONDA_NAME='%CONDA_NAME%' PX4_SOCKET='%PX4_SOCKET%' PX4_INSTANCE='%PX4_INSTANCE%' PX4_BUILD_DIR='%PX4_BUILD_DIR_WSL%' PX4_READY_TIMEOUT_S='%PX4_READY_TIMEOUT_S%' PX4_WAIT_COMMANDER=1 bash '%WSL_SCRIPT_DIR%/wait_px4_ready.sh'"
if not "%errorlevel%"=="0" exit /b 1

if not defined PX4_INITIAL_HEADING_DEG goto skip_heading_alignment
echo Aligning PX4 instance %PX4_INSTANCE% heading to %PX4_INITIAL_HEADING_DEG% degrees...
wsl -d %WSL_DISTRO% -- bash -lc "cd '%PX4_BUILD_DIR_WSL%' && deadline=$((SECONDS + %PX4_READY_TIMEOUT_S%)); while (( SECONDS < deadline )); do ./bin/px4-commander --instance '%PX4_INSTANCE%' set_heading '%PX4_INITIAL_HEADING_DEG%' >/dev/null 2>&1 || true; sleep 2; if ./bin/px4-commander --instance '%PX4_INSTANCE%' check >/dev/null 2>&1; then exit 0; fi; done; echo 'PX4 heading alignment or preflight check timed out.' >&2; exit 1"
if not "%errorlevel%"=="0" exit /b 1
:skip_heading_alignment

echo Setting PX4 instance %PX4_INSTANCE% maximum horizontal velocity to %PX4_MPC_XY_VEL_MAX% m/s...
wsl -d %WSL_DISTRO% -- bash -lc "cd '%PX4_BUILD_DIR_WSL%' && ./bin/px4-param --instance '%PX4_INSTANCE%' set MPC_XY_VEL_MAX '%PX4_MPC_XY_VEL_MAX%'"
if not "%errorlevel%"=="0" exit /b 1

echo Starting PX4 instance %PX4_INSTANCE% MAVLink output to %PX4_WINDOWS_HOST_IP%:%PX4_MAVLINK_REMOTE_PORT%...
echo Stopping existing PX4 instance %PX4_INSTANCE% MAVLink link on local port %PX4_MAVLINK_LOCAL_PORT%...
wsl -d %WSL_DISTRO% -- bash -lc "cd '%PX4_BUILD_DIR_WSL%' && timeout 5s ./bin/px4-mavlink --instance '%PX4_INSTANCE%' stop -u '%PX4_MAVLINK_LOCAL_PORT%' || true"
if defined PX4_MAVLINK_REPLACE_LOCAL_PORT (
    echo Replacing PX4 MAVLink link on local port %PX4_MAVLINK_REPLACE_LOCAL_PORT%...
    wsl -d %WSL_DISTRO% -- bash -lc "cd '%PX4_BUILD_DIR_WSL%' && timeout 5s ./bin/px4-mavlink --instance '%PX4_INSTANCE%' stop -u '%PX4_MAVLINK_REPLACE_LOCAL_PORT%' || true"
)
wsl -d %WSL_DISTRO% -- bash -lc "cd '%PX4_BUILD_DIR_WSL%' && ./bin/px4-mavlink --instance '%PX4_INSTANCE%' start -x -u '%PX4_MAVLINK_LOCAL_PORT%' -r '%PX4_MAVLINK_RATE%' -t '%PX4_WINDOWS_HOST_IP%' -o '%PX4_MAVLINK_REMOTE_PORT%'"
if not "%errorlevel%"=="0" exit /b 1

echo PX4 MAVLink output is ready.
exit /b 0
