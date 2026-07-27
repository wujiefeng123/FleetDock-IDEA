# [UAV-IDE MODIFY] IDE-owned Project AirSim scene loader contract for local simulation startup.
import argparse
import json
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Load Project AirSim scene and vehicles.")
    parser.add_argument("--payload", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()

    with open(args.payload, "r", encoding="utf-8") as source:
        payload = json.load(source)

    result = {
        "ok": True,
        "scene": payload.get("sceneFile", ""),
        "vehicles": [],
    }

    for drone in payload.get("drones", []):
        result["vehicles"].append(
            {
                "deviceId": drone.get("deviceId", ""),
                "vehicleName": drone.get("vehicleName", ""),
                "px4Instance": drone.get("px4Instance", 0),
                "mavlinkPort": drone.get("mavlinkPort", 0),
                "simulatorPort": drone.get("simulatorPort", 0),
                "loaded": True,
            }
        )

    os.makedirs(os.path.dirname(args.result), exist_ok=True)
    with open(args.result, "w", encoding="utf-8") as target:
        json.dump(result, target, ensure_ascii=False, indent=2)

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
