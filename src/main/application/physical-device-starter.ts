// Copyright (c) Jupyter Development Team.
// Distributed under the terms of the Modified BSD License.

// [UAV-IDE MODIFY] Generate a non-destructive first-run notebook only after a
// real PX4 device is explicitly bound to an application.

import * as fs from 'fs';
import * as path from 'path';

export function ensurePhysicalDeviceStarterNotebook(
  workingDirectory: string
): string {
  const demoDirectory = path.join(workingDirectory, 'demo');
  const notebookPath = path.join(
    demoDirectory,
    '00_PX4真机_只读连接检查.ipynb'
  );
  if (fs.existsSync(notebookPath)) {
    return notebookPath;
  }
  fs.mkdirSync(demoDirectory, { recursive: true });
  const cells = [
    {
      cell_type: 'markdown',
      id: 'intro',
      metadata: {},
      source: [
        '# PX4 真机：只读连接检查\n',
        '\n',
        '本示例只建立连接并读取状态，不会解锁、起飞或发送运动指令。运行代码时，由 FleetDock SDK 独占真机端口；IDE 通过运行时状态观察连接。'
      ]
    },
    {
      cell_type: 'code',
      id: 'runtime',
      metadata: {},
      execution_count: null as null,
      outputs: [] as unknown[],
      source: [
        'from pathlib import Path\n',
        'import sys\n',
        '\n',
        'APP_ROOT = next((p for p in (Path.cwd(), *Path.cwd().parents) if (p / "fleetdock_runtime.py").exists()), None)\n',
        'if APP_ROOT is None:\n',
        '    raise RuntimeError("未找到 fleetdock_runtime.py，请在当前应用目录中运行。")\n',
        'if str(APP_ROOT) not in sys.path:\n',
        '    sys.path.insert(0, str(APP_ROOT))\n',
        '\n',
        'from fleetdock_runtime import FleetDock\n',
        'FleetDock.init()\n',
        'aliases = list(FleetDock.info()["devices"])\n',
        'if not aliases:\n',
        '    raise RuntimeError("当前应用没有绑定设备。")\n',
        'print("已绑定设备：", aliases)'
      ]
    },
    {
      cell_type: 'code',
      id: 'observe',
      metadata: {},
      execution_count: null as null,
      outputs: [] as unknown[],
      source: [
        '# 选择第一个绑定设备；多设备应用可改成 FleetDock.devices.<别名>\n',
        'alias = aliases[0]\n',
        'device = getattr(FleetDock.devices, alias)\n',
        'response = device.connect()\n',
        'if not response.ok:\n',
        '    raise RuntimeError(f"连接失败：{response.code.value} / {response.message}")\n',
        '\n',
        'try:\n',
        '    snapshot = device.state_snapshot()\n',
        '    print("设备：", alias)\n',
        '    print("飞行模式：", snapshot.flight_mode)\n',
        '    print("已解锁：", snapshot.armed)\n',
        '    print("飞行中：", snapshot.in_air)\n',
        '    print("电量：", None if snapshot.battery_remaining_fraction is None else f"{snapshot.battery_remaining_fraction * 100:.1f}%")\n',
        '    print("GPS：", snapshot.latitude_deg, snapshot.longitude_deg)\n',
        'finally:\n',
        '    device.close()\n',
        '    print("连接已关闭，端口已释放。")'
      ]
    },
    {
      cell_type: 'markdown',
      id: 'safety-note',
      metadata: {},
      source: [
        '## 继续进行控制测试前\n',
        '\n',
        '本示例不会发送解锁、起飞或运动指令。继续进行控制测试前，台架调试必须拆桨、固定机体，并设置独立急停措施。'
      ]
    }
  ];
  fs.writeFileSync(
    notebookPath,
    `${JSON.stringify(
      {
        cells,
        metadata: {
          kernelspec: {
            display_name: 'Python 3 (FleetDock IDEA)',
            language: 'python',
            name: 'python3'
          },
          language_info: { name: 'python', version: '3.12' }
        },
        nbformat: 4,
        nbformat_minor: 5
      },
      null,
      1
    )}\n`,
    'utf8'
  );
  return notebookPath;
}
