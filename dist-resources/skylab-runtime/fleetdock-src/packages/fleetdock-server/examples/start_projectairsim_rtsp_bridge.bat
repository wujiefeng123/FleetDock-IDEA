@echo off
setlocal

rem Project AirSim RGB camera to local RTSP bridge. This script does not start PX4 or WS.
if not defined PROJECTAIRSIM_DRONE_NAME set "PROJECTAIRSIM_DRONE_NAME=Drone1"
if not defined FLEETDOCK_CAMERA_STREAM_URI set "FLEETDOCK_CAMERA_STREAM_URI=rtsp://127.0.0.1:8554/Drone1_camera_front"
if not defined FLEETDOCK_LOG_LEVEL set "FLEETDOCK_LOG_LEVEL=INFO"

python -c "from fleetdock.simulation import ProjectAirSimRtspBridge" >nul 2>&1
if errorlevel 1 (
    echo FleetDock Project AirSim RTSP dependencies are not installed in the current Python environment.
    echo Run from the repository root: python -m pip install -e ".[projectairsim-rtsp]"
    exit /b 1
)

python "%~dp0..\src\fleetdock-cli\projectairsim_rtsp_bridge.py" ^
    --drone-name "%PROJECTAIRSIM_DRONE_NAME%" ^
    --camera-rtsp-uri "%FLEETDOCK_CAMERA_STREAM_URI%" ^
    --log-level "%FLEETDOCK_LOG_LEVEL%" ^
    %*

set "FLEETDOCK_EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %FLEETDOCK_EXIT_CODE%
