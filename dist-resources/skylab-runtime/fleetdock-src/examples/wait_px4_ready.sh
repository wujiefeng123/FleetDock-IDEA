#!/usr/bin/env bash
set -eu
set -o pipefail

PX4_SOCKET="${PX4_SOCKET:-/tmp/px4-sock-0}"
PX4_INSTANCE="${PX4_INSTANCE:-0}"
PX4_BUILD_DIR="${PX4_BUILD_DIR:-}"
PX4_READY_TIMEOUT_S="${PX4_READY_TIMEOUT_S:-90}"
PX4_WAIT_COMMANDER="${PX4_WAIT_COMMANDER:-0}"
PX4_SOCKET_ONLY="${PX4_SOCKET_ONLY:-0}"
CONDA_NAME="${CONDA_NAME:-fleetdock}"

deadline=$((SECONDS + PX4_READY_TIMEOUT_S))

echo "conda env: ${CONDA_NAME}"
echo "px4 instance: ${PX4_INSTANCE}"
echo "wait px4 socket: ${PX4_SOCKET}"
while (( SECONDS < deadline )); do
    if [[ -S "${PX4_SOCKET}" ]]; then
        break
    fi
    sleep 1
done

if [[ ! -S "${PX4_SOCKET}" ]]; then
    echo "PX4 socket not ready after ${PX4_READY_TIMEOUT_S}s: ${PX4_SOCKET}" >&2
    exit 1
fi

echo "PX4 socket is ready."
if [[ "${PX4_SOCKET_ONLY}" == "1" ]]; then
    exit 0
fi

if [[ -z "${PX4_BUILD_DIR}" ]]; then
    echo "PX4_BUILD_DIR is required to verify the PX4 server." >&2
    exit 1
fi

if [[ "${PX4_WAIT_COMMANDER}" == "1" ]]; then
    readiness_name="commander"
else
    readiness_name="parameter server"
fi

echo "wait px4 ${readiness_name}"
while (( SECONDS < deadline )); do
    if [[ -S "${PX4_SOCKET}" ]]; then
        if [[ "${PX4_WAIT_COMMANDER}" == "1" ]]; then
            if (cd "${PX4_BUILD_DIR}" && timeout 3s ./bin/px4-commander --instance "${PX4_INSTANCE}" status >/dev/null 2>&1); then
                echo "PX4 commander is ready."
                exit 0
            fi
        elif (cd "${PX4_BUILD_DIR}" && timeout 3s ./bin/px4-param --instance "${PX4_INSTANCE}" show MAV_SYS_ID >/dev/null 2>&1); then
            echo "PX4 parameter server is ready."
            exit 0
        fi
    fi
    sleep 1
done

echo "PX4 ${readiness_name} not ready after ${PX4_READY_TIMEOUT_S}s." >&2
exit 1
