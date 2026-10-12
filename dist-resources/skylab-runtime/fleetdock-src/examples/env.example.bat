@echo off
rem Copy this file to env.bat, then uncomment only the values that need local overrides.
rem env.bat stores local paths and simulator choices and must not be committed.

rem set "CONDA_BAT=C:\ProgramData\anaconda3\condabin\conda.bat"
rem set "CONDA_NAME=fleetdock"
rem set "WSL_DISTRO=Ubuntu-24.04"
rem FleetDock examples 目录在 WSL 中的挂载路径。
rem set "WSL_SCRIPT_DIR=/mnt/e/git/github/FleetDock/examples"
rem set "AIRSIM_EXE=F:\runtime\ProjectAirSim\Neighborhood-Windows-UE5.2-PAS_v0.2.0\AirSimNH.exe"
rem Old simulator processes are matched by executable path under the AIRSIM_EXE directory and its subdirectories.
rem AirSim 启动后等待多少秒再继续启动 PX4。
rem set "AIRSIM_STARTUP_WAIT_S=5"
rem Keep the visible simulator window at a practical default size.
rem set "AIRSIM_ARGS=-ResX=1280 -ResY=720 -Windowed -ForceRes"
rem Off-screen rendering and disabled VSync reduce GPU load when only camera streams are required.
rem set "AIRSIM_ARGS=-RenderOffScreen -NoVSync"
rem set "PROJECTAIRSIM_EXAMPLES_DIR=E:\git\github\ProjectAirSim\client\python\example_user_scripts"
rem set "PROJECTAIRSIM_SRC_DIR=E:\git\github\ProjectAirSim\client\python\projectairsim\src"
rem 生成场景的输出文件名，必须是以 .generated.jsonc 结尾的纯文件名。
rem set "PROJECTAIRSIM_SCENE_NAME=scene_fleetdock_px4_single.generated.jsonc"
rem Project AirSim RGB-to-RTSP bridge uses the GStreamer dependencies installed in the fleetdock environment.
rem set "FLEETDOCK_CAMERA_STREAM_URI=rtsp://127.0.0.1:8554/Drone1_camera_front"
rem set "PROJECTAIRSIM_DRONE_NAME=Drone1"
rem set "PROJECTAIRSIM_HOST=127.0.0.1"
rem set "PROJECTAIRSIM_TOPICS_PORT=8989"
rem set "PROJECTAIRSIM_SERVICES_PORT=8990"
rem set "PX4_ROOT_WSL=/mnt/e/git/github/PX4-Autopilot"
rem set "PX4_BUILD_DIR_WSL=/mnt/e/git/github/PX4-Autopilot/build/px4_sitl_default"
rem 单机 PX4 实例、命令 socket、就绪超时和 MAVLink 输出参数。
rem set "PX4_SOCKET=/tmp/px4-sock-0"
rem set "PX4_INSTANCE=0"
rem set "PX4_READY_TIMEOUT_S=180"
rem 留空时跳过 PX4 初始航向对齐。
rem set "PX4_INITIAL_HEADING_DEG=0"
rem 启动脚本会从 WSL 自动检测 Windows 主机地址；配置值不一致时以检测值为准。
rem set "PX4_SIM_HOST_ADDR="
rem set "PX4_WINDOWS_HOST_IP="
rem set "PX4_MAVLINK_LOCAL_PORT=14660"
rem set "PX4_MAVLINK_REMOTE_PORT=14555"
rem set "PX4_MAVLINK_RATE=4000000"
rem SITL horizontal velocity limit in m/s; PX4 permits up to 20.
rem set "PX4_MPC_XY_VEL_MAX=20"

rem Profiles: basic cameras, video RGB, depth RGB/depth, stereo cameras, lidar, radar.
rem For the graphical controller, basic,video provides one front 960x540 RGB stream at 30 FPS.
rem set "FLEETDOCK_CAMERA_FRAME_RATE_HZ=30"
rem set "FLEETDOCK_CAMERA_BITRATE_KBPS=8000"
rem set "FLEETDOCK_CAMERA_GOP_FRAMES=30"
rem set "FLEETDOCK_CAMERA_ENCODER_COMPLEXITY=medium"
rem set "FLEETDOCK_CAMERA_ENABLE_FRAME_SKIP=true"
rem set "FLEETDOCK_WHIP_RTSP_LATENCY_MS=0"
rem set "FLEETDOCK_WHIP_DROP_ON_LATENCY=true"
rem set "PROJECTAIRSIM_ROBOT_PROFILE=basic,video"
rem set "PROJECTAIRSIM_ROBOT_PROFILE=lidar"
rem Comma-separated sensor profiles override the single profile above.
rem set "PROJECTAIRSIM_SENSOR_PROFILES=stereo,lidar"
rem A custom robot file may be complete or inherit through base-robot-config.
rem set "PROJECTAIRSIM_ROBOT_CONFIG_PATH=D:\configs\robot_px4_custom.jsonc"
rem set "PROJECTAIRSIM_SCENE_CONFIG_PATH=D:\configs\scene_px4_custom.jsonc"

rem For multi-drone scenes, only Drone1 uses non-basic sensors by default.
rem Set all or a comma-separated list such as Drone1,Drone3.
rem set "MULTI_PROJECTAIRSIM_SENSOR_DRONES=all"
rem Per-drone profile combinations take precedence; unspecified drones use basic.
rem set "MULTI_PROJECTAIRSIM_SENSOR_PROFILES=Drone1=stereo+lidar;Drone2=depth"

rem set "MULTI_DRONE_COUNT=3"
rem set "MULTI_DRONE_NAME_PREFIX=Drone"
rem set "MULTI_PROJECTAIRSIM_SCENE_NAME=scene_fleetdock_px4_multi.generated.jsonc"
rem set "MULTI_PROJECTAIRSIM_SPAWN_SPACING_M=10.0"
rem set "MULTI_PX4_SIM_MODEL=none_iris"
rem set "MULTI_PX4_SOCKET_PREFIX=/tmp/px4-sock-"
rem set "MULTI_MAVLINK_LOCAL_PORT_BASE=14660"
rem set "MULTI_MAVLINK_REMOTE_PORT_BASE=14555"
rem set "MULTI_PX4_DEFAULT_GCS_PORT_BASE=18570"
rem set "PROJECTAIRSIM_HOME_LATITUDE_DEG=29.61002"
rem set "PROJECTAIRSIM_HOME_LONGITUDE_DEG=106.30514"
rem set "PROJECTAIRSIM_HOME_ALTITUDE_M=293.0"
