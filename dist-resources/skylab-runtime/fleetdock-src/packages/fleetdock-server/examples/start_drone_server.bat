@echo off
setlocal

rem Windows PX4 server demo. The camera or media server must provide the RTSP source.
if not defined FLEETDOCK_PX4_ENDPOINT set "FLEETDOCK_PX4_ENDPOINT=udpin://0.0.0.0:14555"
if not defined FLEETDOCK_DEVICE_ID set "FLEETDOCK_DEVICE_ID=drone-001"
if not defined FLEETDOCK_CAMERA_STREAM_URI set "FLEETDOCK_CAMERA_STREAM_URI=rtsp://127.0.0.1:8554/Drone1_camera_front"
if not defined FLEETDOCK_CAMERA_SOURCE set "FLEETDOCK_CAMERA_SOURCE=unknown"
if not defined FLEETDOCK_WS_HOST set "FLEETDOCK_WS_HOST=127.0.0.1"
if not defined FLEETDOCK_WS_PORT set "FLEETDOCK_WS_PORT=23045"
if not defined FLEETDOCK_SRS_WHIP_URL set "FLEETDOCK_SRS_WHIP_URL=https://live.jpy.wang/rtc/v1/whip/?app=live&stream=drone-001"
if not defined FLEETDOCK_SRS_WHEP_URL set "FLEETDOCK_SRS_WHEP_URL=https://live.jpy.wang/rtc/v1/whep/?app=live&stream=drone-001"
if not defined FLEETDOCK_MEDIA_BACKEND set "FLEETDOCK_MEDIA_BACKEND=gstreamer"
if not defined FLEETDOCK_WHIP_RTSP_LATENCY_MS set "FLEETDOCK_WHIP_RTSP_LATENCY_MS=0"
if not defined FLEETDOCK_WHIP_DROP_ON_LATENCY set "FLEETDOCK_WHIP_DROP_ON_LATENCY=true"

set "FLEETDOCK_DROP_LATENCY_ARG=--drop-on-latency"
if /i "%FLEETDOCK_WHIP_DROP_ON_LATENCY%"=="false" set "FLEETDOCK_DROP_LATENCY_ARG=--no-drop-on-latency"

python -c "import fleetdock_server" >nul 2>&1
if errorlevel 1 (
    echo FleetDock Server is not installed in the current Python environment.
    echo Run: python -m pip install -e "packages/fleetdock-server[drone]"
    exit /b 1
)

python "%~dp0..\src\fleetdock-cli\drone_server.py" ^
    --endpoint "%FLEETDOCK_PX4_ENDPOINT%" ^
    --device-id "%FLEETDOCK_DEVICE_ID%" ^
    --camera-rtsp-uri "%FLEETDOCK_CAMERA_STREAM_URI%" ^
    --camera-source "%FLEETDOCK_CAMERA_SOURCE%" ^
    --ws-host "%FLEETDOCK_WS_HOST%" ^
    --ws-port "%FLEETDOCK_WS_PORT%" ^
    --media-publish-url "%FLEETDOCK_SRS_WHIP_URL%" ^
    --whep-url "%FLEETDOCK_SRS_WHEP_URL%" ^
    --media-backend "%FLEETDOCK_MEDIA_BACKEND%" ^
    --rtsp-latency-ms "%FLEETDOCK_WHIP_RTSP_LATENCY_MS%" ^
    %FLEETDOCK_DROP_LATENCY_ARG% ^
    %*

set "FLEETDOCK_EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %FLEETDOCK_EXIT_CODE%
