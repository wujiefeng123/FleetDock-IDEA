/* [UAV-IDE MODIFY] Real-device diagnostics workbench; the FleetDock runtime remains the only physical-link owner. */

(function () {
  'use strict';

  if (window.jlabDesktopRealDeviceDebugger) {
    return;
  }

  const contexts = new Map();

  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function finite(value) {
    // [UAV-IDE MODIFY] JavaScript coerces null/empty values to zero. Telemetry
    // must keep them unknown or a disconnected vehicle appears to be at 0.
    if (value == null || value === '') return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function text(value, fallback) {
    const result = String(value == null ? '' : value).trim();
    return result || fallback || '-';
  }

  function numberText(value, digits, suffix) {
    const number = finite(value);
    return number == null
      ? '-'
      : number.toFixed(digits == null ? 1 : digits) + (suffix || '');
  }

  function boolState(value) {
    if (value === true) return { state: 'ok', text: '正常' };
    if (value === false) return { state: 'error', text: '异常' };
    return { state: 'unknown', text: '未知' };
  }

  function endpoint(device) {
    return text(
      device?.fleetdock?.params?.endpoint ||
        device?.real?.connectionUri ||
        device?.connectionUri,
      '未配置连接地址'
    );
  }

  function deviceKey(device) {
    return String(device?.id || device?.name || 'physical-device');
  }

  function widgetKey(device) {
    return 'jld-real-debug:' + deviceKey(device);
  }

  function findWidget(lab, key) {
    for (const widget of lab.shell.widgets('main')) {
      if (widget?.node?.dataset?.jldRealDebugKey === key) {
        return widget;
      }
    }
    return null;
  }

  async function createWidget(lab, device) {
    const key = widgetKey(device);
    const existing = findWidget(lab, key);
    if (existing && !existing.isDisposed) {
      lab.shell.activateById(existing.id);
      return existing;
    }
    const created = await lab.commands.execute('launcher:create');
    const widget =
      created && created.node ? created : lab.shell.currentWidget || null;
    if (!widget) {
      throw new Error('无法创建 JupyterLab 真机调试页签。');
    }
    widget.node.dataset.jldRealDebugKey = key;
    widget.node.classList.add('jld-real-debug-host');
    widget.title.label = '真机调试 · ' + text(device?.name, '物理设备');
    widget.title.closable = true;
    const root = document.createElement('div');
    root.className = 'jld-real-debugger';
    root.dataset.deviceId = String(device?.id || '');
    widget.node.appendChild(root);
    // [UAV-IDE MODIFY] The Launcher supplies the host widget only. Hide its
    // default cards so they cannot show through workbench spacing.
    const hideLauncherChildren = () => {
      for (const child of widget.node.children) {
        if (child === root) continue;
        child.setAttribute('data-jld-real-debug-background', 'true');
        child.setAttribute('aria-hidden', 'true');
      }
    };
    hideLauncherChildren();
    widget.__jldRealDebugHostObserver = new MutationObserver(
      hideLauncherChildren
    );
    widget.__jldRealDebugHostObserver.observe(widget.node, {
      childList: true
    });
    lab.shell.activateById(widget.id);
    return widget;
  }

  function kernelStatus(lab) {
    let found = false;
    let busy = false;
    for (const widget of lab.shell.widgets('main')) {
      const sessionContext =
        widget?.sessionContext || widget?.content?.sessionContext;
      if (!sessionContext) continue;
      found = true;
      const status = String(
        sessionContext.kernelDisplayStatus ||
          sessionContext.session?.kernel?.status ||
          ''
      ).toLowerCase();
      if (
        status === 'busy' ||
        status === 'starting' ||
        status === 'restarting'
      ) {
        busy = true;
      }
    }
    return busy ? '代码运行中' : found ? '内核空闲' : '未发现代码内核';
  }

  function firstNumeric(value, prefix) {
    if (typeof value === 'number' && Number.isFinite(value)) {
      return { label: prefix || 'value', value };
    }
    if (!value || typeof value !== 'object') return null;
    for (const key of Object.keys(value)) {
      if (/timestamp|time_usec|time_us|sequence|frame|id$/i.test(key)) {
        continue;
      }
      const found = firstNumeric(value[key], prefix ? prefix + '.' + key : key);
      if (found) return found;
    }
    return null;
  }

  function updateSeries(context, snapshot) {
    if (snapshot?.connected !== true) return;
    for (const sensor of snapshot?.sensors || []) {
      const numeric = firstNumeric(sensor?.sample, '');
      if (!numeric) continue;
      const key = String(sensor.id || sensor.type || 'sensor');
      const series = context.series.get(key) || [];
      series.push({
        value: numeric.value,
        label: numeric.label,
        time: Date.now()
      });
      if (series.length > 60) series.splice(0, series.length - 60);
      context.series.set(key, series);
    }
  }

  function chart(series) {
    if (!series || series.length < 2) {
      return '<div class="jld-rd-muted" style="margin-top:8px">等待曲线数据…</div>';
    }
    const values = series.map(item => item.value);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = Math.max(max - min, 0.000001);
    const points = values
      .map((value, index) => {
        const x = (index / Math.max(1, values.length - 1)) * 100;
        const y = 47 - ((value - min) / span) * 42;
        return x.toFixed(2) + ',' + y.toFixed(2);
      })
      .join(' ');
    return (
      '<svg class="jld-rd-chart" viewBox="0 0 100 52" preserveAspectRatio="none"><polyline points="' +
      esc(points) +
      '"></polyline></svg>'
    );
  }

  function diagnose(context, snapshot, runtimeOnline) {
    const lastResult = context.lastCommandResult;
    if (!runtimeOnline) {
      return {
        level: 'error',
        title: '为什么连不上：FleetDock运行时未启动',
        message:
          '当前没有可响应的应用内核。请打开绑定该设备的应用，并启动一个Notebook或Python内核。',
        evidence: 'IDE没有打开第二条物理连接。'
      };
    }
    if (!snapshot) {
      return {
        level: 'error',
        title: '为什么连不上：设备未绑定当前应用',
        message:
          '当前运行时没有找到该设备。请在设备库中保存后，使用“保存并绑定当前应用”。',
        evidence: '设备ID：' + text(context.device.id)
      };
    }
    if (lastResult && lastResult.ok === false) {
      const preconditions = (lastResult.preconditions || [])
        .map(item => item.message)
        .filter(Boolean);
      return {
        level: 'error',
        title: failureTitle(lastResult),
        message:
          preconditions.join('；') ||
          text(lastResult.message, '飞控拒绝了刚才的调试操作。'),
        evidence:
          '错误码：' +
          text(lastResult.code) +
          '，耗时：' +
          numberText(lastResult.durationMs, 0, ' ms')
      };
    }
    if (snapshot.connected !== true) {
      return {
        level: 'warning',
        title: '当前未连接真机',
        message:
          '运行时已就绪，但SDK尚未建立物理连接。点击“连接”后会由当前内核打开设备端口。',
        evidence: endpoint(context.device)
      };
    }
    if (
      finite(snapshot.telemetryAgeMs) != null &&
      finite(snapshot.telemetryAgeMs) > 3000
    ) {
      return {
        level: 'error',
        title: '遥测已中断',
        message:
          '连接对象仍存在，但超过3秒没有收到标准遥测。请检查数据线、MAVLink转发和飞控供电。',
        evidence: '遥测年龄：' + numberText(snapshot.telemetryAgeMs, 0, ' ms')
      };
    }
    if (snapshot.failsafe === true) {
      return {
        level: 'error',
        title: '飞控正在触发Failsafe',
        message: '暂停新的控制测试，先检查电池、定位、遥控链路和PX4故障信息。',
        evidence: '飞行模式：' + text(snapshot.flightMode)
      };
    }
    if (snapshot.health?.armable === false) {
      return {
        level: 'warning',
        title: '为什么不能解锁：飞控报告不可解锁',
        message: '优先检查EKF定位、Home、传感器校准、Safety Switch和电池状态。',
        evidence:
          'GPS=' +
          stateText(snapshot.health?.globalPositionOk) +
          '，Home=' +
          stateText(snapshot.health?.homePositionOk)
      };
    }
    return {
      level: 'ok',
      title: '连接和基础遥测正常',
      message:
        '当前没有发现阻断性故障。执行解锁或起飞测试前仍需确认现场安全条件。',
      evidence:
        '模式：' +
        text(snapshot.flightMode) +
        '，遥测年龄：' +
        numberText(snapshot.telemetryAgeMs, 0, ' ms')
    };
  }

  function failureTitle(result) {
    if (result.debugCommand === 'unlock_test') return '为什么不能解锁';
    if (result.debugCommand === 'takeoff_test') return '刚才为什么起飞失败';
    if (result.debugCommand === 'return_to_launch_test')
      return '刚才为什么返航失败';
    if (
      result.debugCommand === 'connect' ||
      result.debugCommand === 'reconnect'
    )
      return '为什么连不上';
    return '调试操作失败';
  }

  function stateText(value) {
    return value === true ? '正常' : value === false ? '异常' : '未知';
  }

  function metric(label, value) {
    return (
      '<div class="jld-rd-metric"><span>' +
      esc(label) +
      '</span><strong title="' +
      esc(value) +
      '">' +
      esc(value) +
      '</strong></div>'
    );
  }

  function check(label, value, unknownText) {
    const status = boolState(value);
    return (
      '<div class="jld-rd-check" data-state="' +
      esc(status.state) +
      '"><strong>' +
      esc(label) +
      '</strong><span>' +
      esc(value == null ? unknownText || '尚无数据' : status.text) +
      '</span></div>'
    );
  }

  function renderSensors(context, snapshot) {
    if (snapshot?.connected !== true) {
      return '<div class="jld-rd-empty">连接真机后显示实时传感器数据。</div>';
    }
    const sensors = (snapshot?.sensors || []).slice(0, 8);
    if (!sensors.length) {
      return '<div class="jld-rd-empty">连接成功后将在这里显示可用传感器。</div>';
    }
    return (
      '<div class="jld-rd-sensors">' +
      sensors
        .map(sensor => {
          const key = String(sensor.id || sensor.type || 'sensor');
          const series = context.series.get(key) || [];
          const latest = series[series.length - 1];
          return (
            '<div class="jld-rd-sensor"><div class="jld-rd-sensor-head"><strong>' +
            esc(text(sensor.type || sensor.id, 'sensor')) +
            '</strong><span class="jld-rd-muted">' +
            esc(numberText(sensor.actualRateHz, 1, ' Hz')) +
            '</span></div><div class="jld-rd-muted">' +
            esc(
              latest
                ? latest.label + ' = ' + numberText(latest.value, 3)
                : text(sensor.streamError, '等待采样')
            ) +
            '</div>' +
            chart(series) +
            '</div>'
          );
        })
        .join('') +
      '</div>'
    );
  }

  function renderCommands(snapshot) {
    const commands = (snapshot?.recentCommands || []).slice(-8).reverse();
    if (!commands.length) {
      return '<div class="jld-rd-empty">尚未记录FleetDock标准指令。</div>';
    }
    return (
      '<table class="jld-rd-table"><thead><tr><th>时间</th><th>指令</th><th>结果</th><th>耗时</th></tr></thead><tbody>' +
      commands
        .map(
          item =>
            '<tr><td>' +
            esc(timeOnly(item.completedAt || item.startedAt)) +
            '</td><td>' +
            esc(text(item.command)) +
            '</td><td title="' +
            esc(text(item.message || item.code)) +
            '">' +
            esc(item.ok ? '成功' : text(item.code, '失败')) +
            '</td><td>' +
            esc(numberText(item.durationMs, 0, ' ms')) +
            '</td></tr>'
        )
        .join('') +
      '</tbody></table>'
    );
  }

  function timeOnly(value) {
    const date = new Date(value || 0);
    return Number.isNaN(date.getTime())
      ? '-'
      : date.toLocaleTimeString('zh-CN', { hour12: false });
  }

  function renderTimeline(context, snapshot) {
    const alias = String(snapshot?.alias || '');
    const items = (context.runtimeSnapshot?.timeline || [])
      .filter(item => !alias || item.deviceAlias === alias)
      .slice(-24)
      .reverse();
    if (!items.length) {
      return '<div class="jld-rd-empty">遥测变化、代码指令和飞控错误会按时间显示在这里。</div>';
    }
    return (
      '<div class="jld-rd-timeline">' +
      items
        .map(
          item =>
            '<div class="jld-rd-event" data-severity="' +
            esc(item.severity || 'info') +
            '"><span>' +
            esc(timeOnly(item.timestamp)) +
            '</span><span class="jld-rd-muted">' +
            esc(item.source || '-') +
            '</span><strong title="' +
            esc(item.type || '') +
            '">' +
            esc(item.message || item.type || '-') +
            '</strong></div>'
        )
        .join('') +
      '</div>'
    );
  }

  function render(context) {
    const runtime = context.runtimeSnapshot || {};
    const snapshot = context.deviceSnapshot;
    const diagnosis = diagnose(context, snapshot, runtime.online === true);
    const result = context.lastCommandResult;
    const connected = snapshot?.connected === true;
    // [UAV-IDE MODIFY] A bound runtime snapshot is not the same thing as live
    // telemetry. Never render cached/default state while the SDK link is down.
    const telemetry = connected ? snapshot : null;
    const health = telemetry?.health || {};
    const link = telemetry?.link || {};
    const armed = telemetry?.armed === true;
    const inAir = telemetry?.inAir === true;
    const endpointText = endpoint(context.device);

    context.root.innerHTML =
      '<div class="jld-rd-topbar"><div class="jld-rd-title"><strong>' +
      esc(text(context.device.name, '物理设备')) +
      '</strong><div class="jld-rd-subtitle">' +
      esc(endpointText) +
      '</div></div><div class="jld-rd-badges"><span class="jld-rd-badge" data-state="' +
      (connected ? 'ok' : 'warn') +
      '">' +
      (connected ? 'SDK已连接' : 'SDK未连接') +
      '</span><span class="jld-rd-badge" data-state="' +
      (context.kernelStatus === '代码运行中' ? 'warn' : 'ok') +
      '">' +
      esc(context.kernelStatus) +
      '</span><span class="jld-rd-badge">' +
      esc(text(snapshot?.connectionOwner, 'none')) +
      '</span></div></div>' +
      '<div class="jld-rd-layout"><div class="jld-rd-column"><section class="jld-rd-diagnosis" data-level="' +
      esc(diagnosis.level) +
      '"><h2>' +
      esc(diagnosis.title) +
      '</h2><p>' +
      esc(diagnosis.message) +
      '</p><p class="jld-rd-muted">证据：' +
      esc(diagnosis.evidence) +
      '</p></section>' +
      '<section class="jld-rd-card"><div class="jld-rd-card-title"><span>真机测试</span><span class="jld-rd-muted">每次危险动作单独确认</span></div><div class="jld-rd-card-body"><div class="jld-rd-actions">' +
      '<button class="jld-rd-btn" data-variant="primary" data-debug-action="' +
      (connected ? 'reconnect' : 'connect') +
      '" ' +
      (context.commandRunning ? 'disabled' : '') +
      '>' +
      (connected ? '重新连接' : '连接') +
      '</button><button class="jld-rd-btn" data-debug-action="unlock_test" ' +
      (!connected || context.commandRunning ? 'disabled' : '') +
      '>解锁测试</button><button class="jld-rd-btn" data-debug-action="takeoff_test" ' +
      (!connected || !armed || context.commandRunning ? 'disabled' : '') +
      '>起飞测试</button><button class="jld-rd-btn" data-debug-action="return_to_launch_test" ' +
      (!connected || !inAir || context.commandRunning ? 'disabled' : '') +
      '>返航测试</button><button class="jld-rd-btn" data-variant="danger" data-debug-action="lock" ' +
      (!connected || inAir || context.commandRunning ? 'disabled' : '') +
      '>立即上锁</button></div>' +
      (result
        ? '<div class="jld-rd-command-result" data-ok="' +
          esc(String(result.ok === true)) +
          '"><strong>' +
          esc(result.ok ? '最近操作成功' : '最近操作失败') +
          '</strong><div>' +
          esc(text(result.message || result.code)) +
          '</div>' +
          renderPreconditions(result.preconditions) +
          '</div>'
        : '') +
      '</div></section>' +
      '<section class="jld-rd-card"><div class="jld-rd-card-title">GPS / Home / EKF / 安全状态</div><div class="jld-rd-card-body"><div class="jld-rd-checks">' +
      check('GPS', health.globalPositionOk) +
      check('Home', health.homePositionOk) +
      check('EKF / 本地定位', health.localPositionOk, '暂未取得原始EKF状态') +
      check('Armable', health.armable) +
      check(
        'Failsafe',
        telemetry?.failsafe === false
          ? true
          : telemetry?.failsafe == null
          ? null
          : false
      ) +
      check(
        '遥测新鲜',
        finite(telemetry?.telemetryAgeMs) == null
          ? null
          : finite(telemetry?.telemetryAgeMs) < 3000
      ) +
      '</div></div></section>' +
      '<section class="jld-rd-card"><div class="jld-rd-card-title">MAVLink链路诊断</div><div class="jld-rd-card-body"><div class="jld-rd-metrics">' +
      metric('SDK消息频率', numberText(link.messageRateHz, 1, ' Hz')) +
      metric('遥测年龄', numberText(link.telemetryAgeMs, 0, ' ms')) +
      metric('最近指令耗时', numberText(link.latencyMs, 0, ' ms')) +
      metric(
        '丢包率',
        link.packetLossPercent == null
          ? '底层暂未提供'
          : numberText(link.packetLossPercent, 1, '%')
      ) +
      '</div><p class="jld-rd-muted">消息频率来自FleetDock标准事件；精确MAVLink丢包率需要底层帧序号支持，当前不做伪估算。</p></div></section></div>' +
      '<div class="jld-rd-column"><section class="jld-rd-card"><div class="jld-rd-card-title"><span>实时飞行状态</span><span class="jld-rd-muted">' +
      esc(
        telemetry?.telemetryAgeMs == null
          ? '等待遥测'
          : numberText(telemetry.telemetryAgeMs, 0, ' ms前')
      ) +
      '</span></div><div class="jld-rd-card-body"><div class="jld-rd-metrics">' +
      metric('Roll', numberText(telemetry?.attitude?.roll, 1, '°')) +
      metric('Pitch', numberText(telemetry?.attitude?.pitch, 1, '°')) +
      metric('Yaw', numberText(telemetry?.attitude?.yaw, 1, '°')) +
      metric('飞行模式', text(telemetry?.flightMode)) +
      metric('纬度', numberText(telemetry?.global?.lat, 6)) +
      metric('经度', numberText(telemetry?.global?.lon, 6)) +
      metric(
        '相对高度',
        numberText(telemetry?.global?.relativeAltitudeM, 2, ' m')
      ) +
      metric(
        '电池',
        telemetry?.batteryRemaining == null
          ? '-'
          : numberText(telemetry.batteryRemaining * 100, 1, '%')
      ) +
      metric('速度X', numberText(telemetry?.velocity?.x, 2, ' m/s')) +
      metric('速度Y', numberText(telemetry?.velocity?.y, 2, ' m/s')) +
      metric('速度Z', numberText(telemetry?.velocity?.z, 2, ' m/s')) +
      metric(
        '状态',
        !telemetry
          ? '-'
          : armed
          ? inAir
            ? '已解锁 / 飞行中'
            : '已解锁 / 地面'
          : '已上锁'
      ) +
      '</div></div></section>' +
      '<section class="jld-rd-card"><div class="jld-rd-card-title"><span>传感器数据与曲线</span><span class="jld-rd-muted">最近60个采样点</span></div><div class="jld-rd-card-body">' +
      renderSensors(context, snapshot) +
      '</div></section>' +
      '<section class="jld-rd-card"><div class="jld-rd-card-title">代码运行与最近指令</div><div class="jld-rd-card-body">' +
      renderCommands(snapshot) +
      '</div></section></div></div>' +
      '<section class="jld-rd-card" style="margin-top:12px"><div class="jld-rd-card-title"><span>故障时间线</span><span class="jld-rd-muted">代码指令、遥测变化和调试操作</span></div><div class="jld-rd-card-body">' +
      renderTimeline(context, snapshot) +
      '</div></section>';
  }

  function renderPreconditions(items) {
    if (!Array.isArray(items) || !items.length) return '';
    return (
      '<ul>' +
      items
        .map(item => '<li>' + esc(item.message || item.requirement) + '</li>')
        .join('') +
      '</ul>'
    );
  }

  async function runCommand(context, command) {
    if (context.commandRunning) return;
    const api = window.electronAPI || {};
    if (!api.runFleetDockDebugCommand) {
      context.lastCommandResult = {
        ok: false,
        code: 'DEBUG_API_UNAVAILABLE',
        message: '真机调试指令接口尚未加载。',
        debugCommand: command
      };
      render(context);
      return;
    }
    const params = {};
    if (command === 'unlock_test') {
      if (
        !window.confirm(
          '解锁测试会真实发送解锁指令，并在2秒后自动上锁。请确认已拆桨、固定机体并设置急停措施。'
        )
      )
        return;
    } else if (command === 'takeoff_test') {
      const altitude = Number(
        window.prompt(
          '起飞测试会真实控制飞行器。请输入相对Home的测试高度（米）：',
          '1'
        )
      );
      if (!Number.isFinite(altitude) || altitude < 0.5 || altitude > 10) {
        window.alert('测试高度必须在0.5到10米之间。');
        return;
      }
      if (
        !window.confirm(
          '确认执行真实起飞测试？系统只发送起飞指令，不会自动降落，请保持现场可控。'
        )
      )
        return;
      params.altitude = altitude;
    } else if (command === 'return_to_launch_test') {
      if (
        !window.confirm(
          '确认发送真实返航指令？执行前请确认Home点和返航高度安全。'
        )
      )
        return;
    } else if (command === 'lock') {
      if (!window.confirm('确认向地面设备发送上锁指令？')) return;
    }
    context.commandRunning = true;
    render(context);
    try {
      context.lastCommandResult = await api.runFleetDockDebugCommand({
        deviceId: context.device.id,
        alias: context.deviceSnapshot?.alias || '',
        command,
        params
      });
    } catch (error) {
      context.lastCommandResult = {
        ok: false,
        code: 'DEBUG_COMMAND_ERROR',
        message: String(error?.message || error),
        debugCommand: command
      };
    } finally {
      context.commandRunning = false;
      await poll(context);
    }
  }

  async function poll(context) {
    if (context.polling || context.widget.isDisposed) return;
    context.polling = true;
    try {
      const api = window.electronAPI || {};
      context.kernelStatus = kernelStatus(context.lab);
      context.runtimeSnapshot = api.getFleetDockHudSnapshot
        ? await api.getFleetDockHudSnapshot()
        : {
            ok: false,
            online: false,
            devices: [],
            message: 'HUD接口不可用'
          };
      context.deviceSnapshot = (context.runtimeSnapshot.devices || []).find(
        item =>
          String(item?.deviceId || '') === String(context.device.id || '') ||
          (item?.name &&
            String(item.name) === String(context.device.name || ''))
      );
      updateSeries(context, context.deviceSnapshot);
    } catch (error) {
      context.runtimeSnapshot = {
        ok: false,
        online: false,
        devices: [],
        message: String(error?.message || error)
      };
      context.deviceSnapshot = null;
    } finally {
      context.polling = false;
      render(context);
    }
  }

  async function open(device) {
    const lab = window.jupyterapp || window.jupyterlab;
    if (!lab) {
      throw new Error('JupyterLab尚未完成加载。');
    }
    const key = widgetKey(device);
    let context = contexts.get(key);
    if (context && !context.widget.isDisposed) {
      context.device = device;
      lab.shell.activateById(context.widget.id);
      await poll(context);
      return;
    }
    const widget = await createWidget(lab, device);
    const root = widget.node.querySelector('.jld-real-debugger');
    context = {
      key,
      lab,
      widget,
      root,
      device,
      runtimeSnapshot: null,
      deviceSnapshot: null,
      lastCommandResult: null,
      kernelStatus: '检查中',
      series: new Map(),
      polling: false,
      commandRunning: false,
      timer: null
    };
    contexts.set(key, context);
    root.addEventListener('click', event => {
      const target =
        event.target instanceof Element
          ? event.target.closest('[data-debug-action]')
          : null;
      const command = target?.getAttribute('data-debug-action') || '';
      if (command) void runCommand(context, command);
    });
    if (widget.disposed?.connect) {
      widget.disposed.connect(() => {
        window.clearInterval(context.timer);
        widget.__jldRealDebugHostObserver?.disconnect();
        contexts.delete(key);
      });
    }
    render(context);
    await poll(context);
    context.timer = window.setInterval(() => {
      if (widget.isDisposed) {
        window.clearInterval(context.timer);
        contexts.delete(key);
        return;
      }
      void poll(context);
    }, 800);
  }

  window.jlabDesktopRealDeviceDebugger = {
    open,
    closeAll: function () {
      for (const context of contexts.values()) {
        context.widget.close();
      }
    }
  };
})();
