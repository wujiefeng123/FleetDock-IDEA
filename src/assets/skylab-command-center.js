// [UAV-IDE MODIFY] Provide a right-side FleetDock command center so UAV workflows stay beside, not inside, the code editor.
/* eslint-disable camelcase, @typescript-eslint/no-unused-vars -- [UAV-IDE MODIFY] Preserve published FleetDock payload keys and compatibility helpers during the upstream migration. */
(function () {
  if (window.__fleetDockCommandCenterInstalled) {
    return;
  }
  const api = window.electronAPI || {};
  if (!document.body || typeof api.getSkyLabWorkspaceContext !== 'function') {
    return;
  }
  // [UAV-IDE MODIFY] Do not suppress the dom-ready retry if an early
  // injection ran against the pre-navigation document.
  window.__fleetDockCommandCenterInstalled = true;
  const STORAGE_OPEN = 'jld-skylab-command-center-open';
  const STORAGE_FIRST_OPEN_PREFIX = 'jld-skylab-command-center-first-open:';
  // [UAV-IDE MODIFY] Keep the unfinished publishing workflow reversible while hiding every user-facing entry point.
  const PUBLISH_UI_ENABLED = false;
  const SIMULATION_START_LOCK_STATES = new Set([
    'checking',
    'awaiting-confirmation',
    'stopping-conflicts',
    'starting-scene',
    'starting-px4',
    'loading-world',
    'verifying',
    'starting-media',
    'starting-mavlink',
    'running',
    'stopping'
  ]);
  const SIMULATION_TRANSIENT_STATES = new Set([
    'checking',
    'awaiting-confirmation',
    'stopping-conflicts',
    'starting-scene',
    'starting-px4',
    'loading-world',
    'verifying',
    'starting-media',
    'starting-mavlink',
    'stopping'
  ]);
  // [UAV-IDE MODIFY] Only expose interactions with a visible, verifiable scene effect.
  const SIMULATION_PRIMARY_COMMANDS = [
    'view.next',
    'simulation.pause.toggle',
    'weather.set',
    'simulation.snapshot'
  ];
  const SIMULATION_COMMANDS = {
    'view.next': {
      label: '\u5207\u6362\u89c6\u89d2',
      title: 'world.switch_streaming_view()'
    },
    'simulation.pause.toggle': {
      label: '\u6682\u505c\u4eff\u771f',
      title: '\u6682\u505c\u6216\u7ee7\u7eed\u5f53\u524d\u4eff\u771f'
    },
    'weather.set': {
      label: '\u5929\u6c14\u8bbe\u7f6e',
      title:
        '\u8bbe\u7f6e\u6674\u5929\u3001\u96e8\u5929\u3001\u96ea\u5929\u3001\u96fe\u5929\u6216\u626c\u5c18'
    },
    'simulation.snapshot': {
      label: '\u5237\u65b0\u72b6\u6001',
      title:
        '\u5237\u65b0\u4eff\u771f\u65f6\u95f4\u3001\u6682\u505c\u72b6\u6001\u548c\u65e0\u4eba\u673a\u4f4d\u59ff'
    }
  };

  const state = {
    activeTab: 'app',
    open: false,
    context: null,
    devices: [],
    loadingDevices: false,
    savingApp: false,
    savingDevice: false,
    startingSimulation: false,
    appEditing: false,
    appDraft: {},
    deviceEditorOpen: false,
    deviceDraft: null,
    selectedSimulationDeviceIds: [],
    simulationResult: null,
    // [UAV-IDE MODIFY] Keep lightweight local log rows for the FleetDock simulation status panel.
    simulationLogs: [],
    // [UAV-IDE MODIFY] Track the global Project AirSim scene library used by the simulation selector.
    simulationScenes: [],
    selectedSimulationSceneId: '',
    // [UAV-IDE MODIFY] Track independent UAV simulation environment and lifecycle status in the simulation tab.
    simulationEnvironment: null,
    simulationEnvironmentChecking: false,
    simulationEnvironmentCheckId: 0,
    simulationStatus: null,
    stoppingSimulation: false,
    // [UAV-IDE MODIFY] Keep QGC discovery and launch feedback inside the ground-station tab.
    groundStationStatus: null,
    groundStationLoading: false,
    groundStationLaunching: false,
    groundStationClosing: false,
    // [UAV-IDE MODIFY] Keep runtime interactions and diagnostics local to the simulation sidebar.
    simulationCommandPending: 0,
    simulationCommandResult: null,
    simulationDiagnostics: null,
    simulationWeatherOpen: false,
    simulationWeatherPreset: 'clear',
    sceneManagerOpen: false,
    simulationConfigDraft: {
      px4Path: '',
      scenePackageDirectory: ''
    },
    // [UAV-IDE MODIFY] Track the scene selected inside the maintenance list independently from inline rename state.
    sceneManagerSelectedId: '',
    sceneDraft: null,
    savingScene: false,
    // [UAV-IDE MODIFY] Keep FleetDock HUD telemetry read-only and scoped to the simulation command-center tab.
    hudOpen: false,
    hudSnapshot: null,
    hudLoading: false,
    hudPinnedDeviceIds: [],
    hudClosedDeviceIds: [],
    // [UAV-IDE MODIFY] Keep the selected developer sensor view independent from HUD polling refreshes.
    sensorPanel: null,
    cameraMedia: {
      vehicleName: '',
      streams: [],
      error: '',
      loading: false
    },
    toast: ''
  };

  // [UAV-IDE MODIFY] Each camera owns an independent peer connection so every vehicle can play concurrently.
  const cameraPlayers = new Map();

  // [UAV-IDE MODIFY] Mirror the supported PX4/Project AirSim device taxonomy in the browser UI.
  const DEVICE_CATEGORY_LABELS = {
    uav: '\u65e0\u4eba\u673a',
    ugv: '\u65e0\u4eba\u8f66',
    usv: '\u65e0\u4eba\u8239',
    humanoid: '\u4eba\u5f62\u673a\u5668\u4eba',
    quadruped: '\u56db\u8db3\u673a\u5668\u4eba'
  };
  const RUNTIME_KIND_LABELS = {
    simulation: '\u4eff\u771f\u8bbe\u5907',
    physical: '\u7269\u7406\u8bbe\u5907'
  };
  const UAV_AIRFRAME_PROFILES = {
    quadrotor: {
      label: '\u56db\u65cb\u7ffc',
      px4AutostartId: 10016,
      projectAirsimAirframe: 'quadrotor-x',
      robotTemplate: 'robot_quadrotor_px4.jsonc'
    },
    hexarotor: {
      label: '\u516d\u65cb\u7ffc',
      px4AutostartId: 10016,
      projectAirsimAirframe: 'hexarotor-x',
      robotTemplate: 'robot_hexarotor_px4.jsonc'
    },
    vtol_tailsitter: {
      label: '\u5782\u76f4\u7ffc Tailsitter',
      px4AutostartId: 1041,
      projectAirsimAirframe: 'vtol-quad-x-tailsitter',
      robotTemplate: 'robot_vtol_tailsitter_px4.jsonc'
    },
    vtol_tiltrotor: {
      label: '\u5782\u76f4\u7ffc Tiltrotor',
      px4AutostartId: 1042,
      projectAirsimAirframe: 'vtol-quad-tiltrotor',
      robotTemplate: 'robot_vtol_tiltrotor_px4.jsonc'
    }
  };

  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function ensureArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function ensureRoot() {
    let root = document.getElementById('jld-skylab-command-center');
    if (!root) {
      root = document.createElement('div');
      root.id = 'jld-skylab-command-center';
      root.className = 'jld-skylab-center';
      document.body.appendChild(root);
      root.addEventListener('click', handleClick);
      root.addEventListener('input', handleInput);
      root.addEventListener('change', handleInput);
      // [UAV-IDE MODIFY] Let the camera viewer move without changing any media or simulation behavior.
      root.addEventListener('pointerdown', handleSensorPanelPointerDown);
    }
    root.setAttribute('data-open', state.open ? '1' : '0');
    return root;
  }

  function toast(message) {
    state.toast = message;
    render();
    window.clearTimeout(window.__jldSkyLabCenterToastTimer);
    window.__jldSkyLabCenterToastTimer = window.setTimeout(() => {
      state.toast = '';
      render();
    }, 2600);
  }

  function normalizeContext(context) {
    const ctx = context || {};
    return {
      ...ctx,
      appCode: ctx.appCode || ctx.name || '',
      name: ctx.name || ctx.appCode || '当前应用',
      version: ctx.version || '1.0.0',
      entryFile: ctx.entryFile || 'main.py',
      maxRunSeconds: Number(ctx.maxRunSeconds) || 300,
      runtimeLabel: ctx.runtimeLabel || 'Python',
      pythonPath: ctx.pythonPath || '',
      description: ctx.description || '',
      changeLog: ctx.changeLog || '',
      aiGateway: ctx.aiGateway || { timeoutMs: 15000 },
      simulator: ctx.simulator || {},
      groundStation: ctx.groundStation || {},
      envVars: ensureArray(ctx.envVars)
    };
  }

  async function refreshContext() {
    try {
      state.context = normalizeContext(
        api.getSkyLabWorkspaceContext
          ? await api.getSkyLabWorkspaceContext()
          : {}
      );
      if (!state.appEditing) {
        state.appDraft = buildAppDraft();
      }
    } catch (error) {
      state.context = normalizeContext({});
      console.warn('Failed to load FleetDock IDEA workspace context', error);
    }
  }

  async function refreshDevices(renderBefore) {
    state.loadingDevices = true;
    if (renderBefore !== false) {
      render();
    }
    try {
      state.devices = api.loadDroneDevices ? await api.loadDroneDevices() : [];
      syncSimulationSelection();
    } catch (error) {
      console.warn('Failed to load drone devices', error);
      toast('设备列表加载失败');
    } finally {
      state.loadingDevices = false;
      render();
    }
  }

  // [UAV-IDE MODIFY] Load the global Project AirSim scene list for the inline simulation selector.
  async function refreshSimulationScenes(renderBefore) {
    if (renderBefore !== false) {
      render();
    }
    try {
      const result = api.listUavSimulationScenes
        ? await api.listUavSimulationScenes()
        : {};
      state.simulationScenes = ensureArray(result?.scenes);
      state.selectedSimulationSceneId = result?.selectedSceneId || '';
      state.simulationConfigDraft = {
        px4Path: result?.px4Path || '',
        scenePackageDirectory: result?.scenePackageDirectory || ''
      };
    } catch (error) {
      state.simulationScenes = [];
      state.selectedSimulationSceneId = '';
      console.warn('Failed to load Project AirSim scenes', error);
      toast('场景列表加载失败');
    } finally {
      render();
    }
  }

  // [UAV-IDE MODIFY] Let users re-check the isolated UAV simulation prerequisites from the simulation tab.
  async function refreshSimulationEnvironment(renderBefore) {
    if (state.simulationEnvironmentChecking) {
      if (renderBefore !== false) {
        render();
      }
      return;
    }
    const checkId = ++state.simulationEnvironmentCheckId;
    state.simulationEnvironmentChecking = true;
    if (renderBefore !== false) {
      render();
    }
    try {
      const report = api.checkUavSimulationEnvironment
        ? await api.checkUavSimulationEnvironment()
        : null;
      if (checkId === state.simulationEnvironmentCheckId) {
        state.simulationEnvironment = report;
      }
    } catch (error) {
      if (checkId === state.simulationEnvironmentCheckId) {
        state.simulationEnvironment = {
          ok: false,
          items: [],
          checkedAt: new Date().toISOString(),
          error: String(error && error.message ? error.message : error)
        };
        toast('仿真环境检测失败');
      }
    } finally {
      if (checkId === state.simulationEnvironmentCheckId) {
        state.simulationEnvironmentChecking = false;
        render();
      }
    }
  }

  function invalidateSimulationEnvironment() {
    // [UAV-IDE MODIFY] Ignore an in-flight result after scene or runtime paths change.
    state.simulationEnvironmentCheckId += 1;
    state.simulationEnvironmentChecking = false;
    state.simulationEnvironment = null;
  }

  function simulationEnvironmentFailureMessage(report) {
    const reasons = ensureArray(report?.items)
      .filter(item => !item?.ok)
      .map(item =>
        [item.label, item.message, item.suggestion].filter(Boolean).join('：')
      )
      .filter(Boolean);
    if (report?.error) {
      reasons.unshift(String(report.error));
    }
    return reasons.length
      ? reasons.join('\n')
      : '\u4eff\u771f\u73af\u5883\u68c0\u6d4b\u672a\u901a\u8fc7\uff0c\u8bf7\u91cd\u65b0\u68c0\u6d4b\u540e\u67e5\u770b\u5931\u8d25\u9879\u3002';
  }

  function ensureSimulationEnvironmentReady() {
    if (state.simulationEnvironmentChecking) {
      toast(
        '\u4eff\u771f\u73af\u5883\u6b63\u5728\u68c0\u6d4b\uff0c\u8bf7\u7b49\u5f85\u68c0\u6d4b\u5b8c\u6bd5'
      );
      return false;
    }
    if (!state.simulationEnvironment?.checkedAt) {
      toast(
        '\u8bf7\u7b49\u5f85\u4eff\u771f\u73af\u5883\u68c0\u6d4b\u5b8c\u6bd5'
      );
      void refreshSimulationEnvironment(false);
      return false;
    }
    if (!state.simulationEnvironment.ok) {
      const message = simulationEnvironmentFailureMessage(
        state.simulationEnvironment
      );
      toast(
        '\u4eff\u771f\u73af\u5883\u68c0\u6d4b\u672a\u901a\u8fc7\uff0c\u8bf7\u67e5\u770b\u5931\u8d25\u539f\u56e0'
      );
      window.alert(
        '\u4eff\u771f\u73af\u5883\u68c0\u6d4b\u672a\u901a\u8fc7\uff1a\n\n' +
          message
      );
      return false;
    }
    return true;
  }

  // [UAV-IDE MODIFY] Poll the independent UAV simulation status on demand without touching Jupyter Server state.
  async function refreshUavSimulationStatus(renderBefore) {
    if (renderBefore !== false) {
      render();
    }
    try {
      state.simulationStatus = api.getUavSimulationStatus
        ? await api.getUavSimulationStatus()
        : null;
      if (
        ['idle', 'failed'].includes(state.simulationStatus?.state) &&
        state.cameraMedia.streams.length
      ) {
        resetCameraMedia();
      }
      syncEmbeddedHudStateFromSimulation();
    } catch (error) {
      console.warn('Failed to load UAV simulation status', error);
    } finally {
      render();
      scheduleHudPolling();
      if (shouldPollSimulationStatus()) {
        scheduleSimulationStatusPolling({ skipImmediate: true });
      } else {
        window.clearInterval(window.__jldSkyLabSimulationStatusTimer);
      }
    }
  }

  async function refreshGroundStationStatus(renderBefore) {
    state.groundStationLoading = true;
    if (renderBefore !== false) {
      render();
    }
    try {
      state.groundStationStatus = api.getGroundStationStatus
        ? await api.getGroundStationStatus()
        : {
            installed: false,
            running: false,
            executablePath: '',
            version: '',
            message: '当前版本未提供地面站集成接口。'
          };
    } catch (error) {
      state.groundStationStatus = {
        installed: false,
        running: false,
        executablePath: '',
        version: '',
        message: String(error?.message || error)
      };
    } finally {
      state.groundStationLoading = false;
      render();
    }
  }

  async function launchGroundStation() {
    if (
      state.groundStationLaunching ||
      state.groundStationClosing ||
      !api.launchGroundStation
    ) {
      return;
    }
    state.groundStationLaunching = true;
    render();
    try {
      state.groundStationStatus = await api.launchGroundStation();
      toast('QGroundControl 已启动');
    } catch (error) {
      toast(String(error?.message || error));
    } finally {
      state.groundStationLaunching = false;
      render();
    }
  }

  async function closeGroundStation() {
    if (
      state.groundStationLaunching ||
      state.groundStationClosing ||
      !api.closeGroundStation
    ) {
      return;
    }
    state.groundStationClosing = true;
    render();
    try {
      state.groundStationStatus = await api.closeGroundStation();
      toast('QGroundControl 已关闭');
    } catch (error) {
      toast(String(error?.message || error));
    } finally {
      state.groundStationClosing = false;
      render();
    }
  }

  async function selectGroundStationExecutable() {
    if (!api.selectGroundStationExecutable) {
      return;
    }
    try {
      state.groundStationStatus = await api.selectGroundStationExecutable();
      render();
    } catch (error) {
      toast(String(error?.message || error));
    }
  }

  async function openGroundStationInstallDirectory() {
    try {
      await api.openGroundStationInstallDirectory?.();
    } catch (error) {
      toast(String(error?.message || error));
    }
  }

  function normalizeSimulationState(value) {
    return String(value || '').toLowerCase();
  }

  function currentSimulationLifecycleState() {
    return normalizeSimulationState(
      state.simulationStatus?.state || state.simulationResult?.state || ''
    );
  }

  function isSimulationRuntimeView() {
    const lifecycleState = currentSimulationLifecycleState();
    return lifecycleState === 'running' || lifecycleState === 'stopping';
  }

  // [UAV-IDE MODIFY] Keep the embedded HUD aligned with the active managed simulation run.
  function syncEmbeddedHudStateFromSimulation() {
    if (isSimulationRuntimeView()) {
      state.hudOpen = true;
      const activeDroneIds = ensureArray(state.simulationStatus?.drones)
        .map(drone => String(drone?.droneId || '').trim())
        .filter(Boolean);
      if (activeDroneIds.length) {
        state.hudPinnedDeviceIds = activeDroneIds;
      }
      return;
    }
    state.hudOpen = false;
    if (
      currentSimulationLifecycleState() === 'idle' ||
      currentSimulationLifecycleState() === 'failed'
    ) {
      state.hudSnapshot = null;
      state.hudLoading = false;
    }
  }

  function shouldPollSimulationStatus() {
    return (
      state.startingSimulation ||
      SIMULATION_TRANSIENT_STATES.has(currentSimulationLifecycleState())
    );
  }

  function scheduleSimulationStatusPolling(options) {
    window.clearInterval(window.__jldSkyLabSimulationStatusTimer);
    if (!shouldPollSimulationStatus()) {
      return;
    }
    if (!options?.skipImmediate) {
      void refreshUavSimulationStatus(false);
    }
    window.__jldSkyLabSimulationStatusTimer = window.setInterval(() => {
      void refreshUavSimulationStatus(false);
    }, 1000);
  }

  function isSimulationStartLocked() {
    return (
      state.startingSimulation ||
      SIMULATION_START_LOCK_STATES.has(currentSimulationLifecycleState())
    );
  }

  function simulationProgressForState(lifecycleState) {
    const progressByState = {
      checking: 12,
      'awaiting-confirmation': 18,
      'stopping-conflicts': 25,
      'starting-scene': 38,
      'starting-px4': 52,
      'loading-world': 68,
      verifying: 82,
      'starting-media': 88,
      'starting-mavlink': 92,
      running: 100,
      stopping: 40,
      failed: 0,
      idle: 0
    };
    return progressByState[lifecycleState] ?? 0;
  }

  function simulationStartButtonAttrs() {
    return isSimulationStartLocked() ? ' disabled aria-disabled="true"' : '';
  }

  function simulationStartButtonLabel() {
    const lifecycleState = currentSimulationLifecycleState();
    if (lifecycleState === 'running') {
      return '\u5df2\u8fd0\u884c';
    }
    if (
      state.startingSimulation ||
      SIMULATION_TRANSIENT_STATES.has(lifecycleState)
    ) {
      return '\u542f\u52a8\u4e2d...';
    }
    return '&#9654; \u542f\u52a8\u4eff\u771f';
  }

  function simulationOverviewStatus() {
    const status = state.simulationStatus || {};
    const result = state.simulationResult || {};
    const lifecycleState = currentSimulationLifecycleState();
    if (
      state.startingSimulation ||
      SIMULATION_TRANSIENT_STATES.has(lifecycleState)
    ) {
      return status.title || result.title || '\u542f\u52a8\u4e2d';
    }
    if (lifecycleState === 'running') {
      return status.title || result.title || '\u4eff\u771f\u5df2\u8fd0\u884c';
    }
    return result.title || status.title || '\u672a\u542f\u52a8';
  }

  function syncSimulationSelection() {
    const simulationDevices = getSimulationDevices();
    const existingIds = new Set(simulationDevices.map(device => device.id));
    state.selectedSimulationDeviceIds = state.selectedSimulationDeviceIds.filter(
      id => existingIds.has(id)
    );
    if (!state.selectedSimulationDeviceIds.length) {
      state.selectedSimulationDeviceIds = simulationDevices
        .slice(0, 2)
        .map(device => device.id);
    }
    state.hudPinnedDeviceIds = state.hudPinnedDeviceIds.filter(id =>
      existingIds.has(id)
    );
  }

  function normalizeTab(tab) {
    if (tab === 'device') {
      return 'devices';
    }
    const visibleTabs = ['app', 'devices', 'simulation', 'groundstation'];
    if (PUBLISH_UI_ENABLED) {
      visibleTabs.push('publish');
    }
    return visibleTabs.includes(tab) ? tab : 'app';
  }

  function openCenter(tab) {
    state.activeTab = normalizeTab(tab || state.activeTab || 'app');
    state.open = true;
    localStorage.setItem(STORAGE_OPEN, '1');
    render();
    if (state.activeTab === 'devices' || state.activeTab === 'simulation') {
      void refreshDevices();
    }
    if (state.activeTab === 'simulation') {
      void refreshSimulationScenes(false);
      void refreshSimulationEnvironment(false);
      void refreshUavSimulationStatus(false);
      scheduleHudPolling();
      scheduleCameraStreams();
    } else if (state.activeTab === 'groundstation') {
      void refreshGroundStationStatus(false);
      void refreshUavSimulationStatus(false);
      window.clearInterval(window.__jldSkyLabHudTimer);
      closeCameraPlayers();
    } else {
      window.clearInterval(window.__jldSkyLabHudTimer);
      closeCameraPlayers();
    }
  }

  function closeCenter() {
    state.open = false;
    window.clearInterval(window.__jldSkyLabHudTimer);
    closeCameraPlayers();
    localStorage.setItem(STORAGE_OPEN, '0');
    render();
  }

  function getConnectionURI(device) {
    const telemetry = device?.telemetry || {};
    if (telemetry.uri) {
      return telemetry.uri;
    }
    const protocol = telemetry.protocol || 'udp';
    const host = telemetry.bindHost || telemetry.remoteHost || '0.0.0.0';
    const port = telemetry.bindPort || telemetry.remotePort || '';
    return port ? protocol + '://' + host + ':' + port : '未配置连接地址';
  }

  // [UAV-IDE MODIFY] Automatic simulation templates do not have a user-entered connection until simulation startup.
  function getDeviceSummaryText(device) {
    if (!device) {
      return '未配置设备';
    }
    if (
      device.source === 'simulation' &&
      device.simulation?.px4StartMode !== 'manual-connect'
    ) {
      return '启动仿真后生成连接方式并回写环境变量';
    }
    return getConnectionURI(device);
  }

  function getTelemetryPort(device, fallback) {
    const telemetry = device?.telemetry || {};
    if (telemetry.bindPort || telemetry.remotePort) {
      return String(telemetry.bindPort || telemetry.remotePort);
    }
    const match = getConnectionURI(device).match(/:(\d+)(?:\D*$|$)/);
    return match ? match[1] : String(fallback);
  }

  function getDeviceName(device, index) {
    return device?.name || device?.id || 'drone' + (index + 1);
  }

  function getVirtualCount() {
    return state.devices.filter(device => device.accessType !== 'physical')
      .length;
  }

  function getPhysicalCount() {
    return state.devices.length - getVirtualCount();
  }

  function getEnvCount() {
    return ensureArray(state.context?.envVars).length;
  }

  // [UAV-IDE MODIFY] SkySim only consumes UAV simulation devices that both PX4 and Project AirSim can represent.
  function isProjectAirsimSimulationUav(device) {
    return (
      !!device &&
      device.enabled !== false &&
      (device.deviceCategory || 'uav') === 'uav' &&
      (device.runtimeKind ||
        (device.source === 'real' ? 'physical' : 'simulation')) ===
        'simulation' &&
      (device.simulation?.engine || 'project-airsim-px4') ===
        'project-airsim-px4'
    );
  }

  function getSimulationDevices() {
    return state.devices.filter(isProjectAirsimSimulationUav);
  }

  function getUavAirframeProfile(type) {
    return UAV_AIRFRAME_PROFILES[type] || UAV_AIRFRAME_PROFILES.quadrotor;
  }

  function isPublishReady() {
    const ctx = state.context || {};
    return !!(ctx.name && ctx.entryFileExists && ctx.requirementsExists);
  }

  function isSimulatorConfigured() {
    const simulator = state.context?.simulator || {};
    return !!(
      simulator.engine ||
      simulator.px4WorkingDirectory ||
      simulator.airSimExecutable ||
      simulator.airSimPath ||
      simulator.sitlCommand
    );
  }

  function buildAppDraft() {
    const ctx = normalizeContext(state.context);
    return {
      appCode: ctx.appCode,
      name: ctx.name,
      version: ctx.version,
      entryFile: ctx.entryFile,
      maxRunSeconds: ctx.maxRunSeconds,
      runtimeLabel: ctx.runtimeLabel,
      pythonPath: ctx.pythonPath,
      description: ctx.description,
      changeLog: ctx.changeLog
    };
  }

  function buildAppPayload(overrides) {
    const ctx = normalizeContext(state.context);
    const overrideValues = overrides || {};
    return {
      ...ctx,
      ...state.appDraft,
      workingDirectory: ctx.workingDirectory,
      maxRunSeconds:
        Number(state.appDraft.maxRunSeconds) || ctx.maxRunSeconds || 300,
      envVars: ensureArray(overrideValues.envVars || ctx.envVars),
      aiGateway: {
        ...ctx.aiGateway,
        timeoutMs: Number(ctx.aiGateway?.timeoutMs) || 15000
      },
      simulator: ctx.simulator || {},
      groundStation: ctx.groundStation || {}
    };
  }

  async function saveAppQuickEdit() {
    if (!api.saveApplicationSettings) {
      toast('当前应用保存接口未接入');
      return;
    }
    if (!String(state.appDraft.name || '').trim()) {
      toast('请先填写应用名称');
      return;
    }
    state.savingApp = true;
    render();
    try {
      await api.saveApplicationSettings(buildAppPayload());
      await refreshContext();
      state.appEditing = false;
      toast('应用基础信息已保存');
    } catch (error) {
      toast(String(error && error.message ? error.message : error));
    } finally {
      state.savingApp = false;
      render();
    }
  }

  function defaultDeviceDraft(source) {
    const index = state.devices.length + 1;
    const port = 14549 + index;
    const deviceSource = source || 'simulation';
    const runtimeKind = deviceSource === 'real' ? 'physical' : 'simulation';
    const airframe = getUavAirframeProfile('quadrotor');
    return {
      name: 'drone' + index,
      deviceCategory: 'uav',
      runtimeKind,
      enabled: true,
      description: '',
      source: deviceSource,
      accessType: deviceSource === 'real' ? 'physical' : 'virtual',
      // [UAV-IDE MODIFY] Device env var sync is now an explicit add-device form option instead of an implicit default.
      envVarSync: false,
      envVarName: '',
      protocol: 'udp',
      mode: 'listen',
      host: '0.0.0.0',
      port,
      connectionUri: 'udp://0.0.0.0:' + port,
      systemId: index,
      airsimVehicleName: 'Drone' + index,
      airframeType: 'quadrotor',
      px4AutostartId: airframe.px4AutostartId,
      projectAirsimAirframe: airframe.projectAirsimAirframe,
      robotTemplate: airframe.robotTemplate,
      model: 'quadrotor',
      px4StartMode: deviceSource === 'real' ? 'manual-connect' : 'ide-managed',
      readOnly: true,
      allowCommand: false,
      heartbeatCheck: true,
      connectionKind: 'udp',
      serialPort: '',
      baudRate: 57600
    };
  }

  function deviceToDraft(device) {
    const telemetry = device?.telemetry || {};
    const airframeType =
      device?.uav?.airframeType || device?.simulation?.model || 'quadrotor';
    const airframe = getUavAirframeProfile(airframeType);
    return {
      id: device?.id || '',
      name: device?.name || '',
      deviceCategory:
        device?.deviceCategory ||
        (device?.deviceType === 'drone' ? 'uav' : device?.deviceType) ||
        'uav',
      runtimeKind:
        device?.runtimeKind ||
        (device?.source === 'real' || device?.accessType === 'physical'
          ? 'physical'
          : 'simulation'),
      enabled: device?.enabled !== false,
      description: device?.description || '',
      source:
        device?.source ||
        (device?.accessType === 'physical' ? 'real' : 'simulation'),
      accessType: device?.accessType || 'virtual',
      envVarSync: !!device?.envVarName,
      envVarName: device?.envVarName || '',
      protocol: telemetry.protocol || 'udp',
      mode: telemetry.mode || 'listen',
      host: telemetry.bindHost || telemetry.remoteHost || '0.0.0.0',
      port: telemetry.bindPort || telemetry.remotePort || 14550,
      connectionUri: getConnectionURI(device),
      systemId: device?.systemId || 1,
      airsimVehicleName:
        device?.simulation?.airsimVehicleName ||
        device?.airsimVehicleName ||
        'Drone1',
      airframeType,
      px4AutostartId: airframe.px4AutostartId,
      projectAirsimAirframe: airframe.projectAirsimAirframe,
      robotTemplate: airframe.robotTemplate,
      model: airframeType,
      px4StartMode: device?.simulation?.px4StartMode || 'ide-managed',
      readOnly: device?.real?.readOnly !== false,
      allowCommand: !!device?.real?.allowCommand,
      heartbeatCheck: device?.real?.heartbeatCheck !== false,
      connectionKind: device?.real?.connectionKind || 'udp',
      serialPort: device?.real?.serialPort || '',
      baudRate: device?.real?.baudRate || 57600
    };
  }

  // [UAV-IDE MODIFY] Keep the add-device form simple while persisting into the existing telemetry shape.
  function parseDeviceConnectionUri(uri, fallbackPort) {
    const text = String(uri || '').trim();
    const match = text.match(
      /^([a-zA-Z][a-zA-Z0-9_-]*):\/\/([^:\/\s]+)(?::(\d+))?/
    );
    return {
      protocol: match ? match[1].toLowerCase() : 'udp',
      host: match ? match[2] : '0.0.0.0',
      port:
        match && match[3] ? Number(match[3]) : Number(fallbackPort) || 14550,
      uri: text
    };
  }

  function buildDevicePayload() {
    const draft = state.deviceDraft || defaultDeviceDraft();
    const isReal = draft.source === 'real';
    const runtimeKind =
      draft.runtimeKind || (isReal ? 'physical' : 'simulation');
    const deviceCategory = draft.deviceCategory || 'uav';
    const airframeType = draft.airframeType || draft.model || 'quadrotor';
    const airframe = getUavAirframeProfile(airframeType);
    const px4StartMode = isReal
      ? 'manual-connect'
      : draft.px4StartMode || 'ide-managed';
    const manual = px4StartMode === 'manual-connect';
    const connection = parseDeviceConnectionUri(
      draft.connectionUri,
      draft.port
    );
    const mode = manual || isReal ? 'listen' : draft.mode || 'listen';
    const port =
      manual || isReal ? connection.port : Number(draft.port) || 14550;
    return {
      id: draft.id || undefined,
      name: String(draft.name || '').trim(),
      deviceCategory,
      runtimeKind,
      enabled: draft.enabled !== false,
      description: String(draft.description || '').trim(),
      source: draft.source || 'simulation',
      accessType: isReal ? 'physical' : 'virtual',
      envVarName: draft.envVarSync ? String(draft.envVarName || '').trim() : '',
      telemetry: {
        protocol:
          manual || isReal ? connection.protocol : draft.protocol || 'udp',
        mode,
        bindHost:
          mode === 'listen'
            ? manual || isReal
              ? connection.host
              : draft.host || '0.0.0.0'
            : undefined,
        bindPort: mode === 'listen' ? port : undefined,
        remoteHost:
          mode === 'dial'
            ? manual || isReal
              ? connection.host
              : draft.host || '127.0.0.1'
            : undefined,
        remotePort: mode === 'dial' ? port : undefined
      },
      systemId: Number(draft.systemId) || 1,
      airsimVehicleName: draft.airsimVehicleName || draft.name || 'Drone1',
      uav: {
        airframeType,
        px4AutostartId: airframe.px4AutostartId,
        projectAirsimAirframe: airframe.projectAirsimAirframe,
        robotTemplate: airframe.robotTemplate,
        vehicleName: draft.airsimVehicleName || draft.name || 'Drone1',
        systemId: Number(draft.systemId) || 1
      },
      simulation: {
        model: airframeType,
        engine: 'project-airsim-px4',
        airsimVehicleName: draft.airsimVehicleName || draft.name || 'Drone1',
        sceneActorName: draft.airsimVehicleName || draft.name || 'Drone1',
        startLanded: true,
        systemIdStrategy: 'auto',
        mavlinkPortStrategy: 'auto',
        px4InstanceStrategy: 'auto',
        px4StartMode,
        runtime: {
          state: 'not-started'
        }
      },
      real: {
        connectionKind: connection.protocol === 'tcp' ? 'tcp' : 'udp',
        host: connection.host,
        port,
        serialPort: draft.serialPort || '',
        baudRate: Number(draft.baudRate) || 57600,
        readOnly: draft.readOnly !== false,
        allowCommand: !!draft.allowCommand,
        heartbeatCheck: draft.heartbeatCheck !== false
      }
    };
  }

  // [UAV-IDE MODIFY] Allow the add-device form to create or reuse device-drone env vars without opening the full app settings dialog.
  function getDroneEnvVars() {
    return ensureArray(state.context?.envVars).filter(
      envVar => envVar?.type === 'Drone' || envVar?.type === 'DroneAddr'
    );
  }

  async function syncDeviceEnvVarFromDraft(devicePayload) {
    const draft = state.deviceDraft || {};
    if (!draft.envVarSync) {
      return;
    }
    if (!api.saveApplicationSettings) {
      toast('当前应用环境变量保存接口未接入');
      return;
    }
    const envVarName = String(draft.envVarName || '').trim();
    if (!envVarName) {
      throw new Error('请填写环境变量名称');
    }
    const envVars = ensureArray(state.context?.envVars).map(envVar => ({
      ...envVar
    }));
    const existingIndex = envVars.findIndex(
      envVar =>
        String(envVar?.name || '')
          .trim()
          .toLowerCase() === envVarName.toLowerCase()
    );
    const connectionUri =
      devicePayload.source === 'simulation' &&
      devicePayload.simulation?.px4StartMode === 'ide-managed'
        ? ''
        : getConnectionURI(devicePayload);
    if (existingIndex >= 0) {
      if (
        envVars[existingIndex].type !== 'Drone' &&
        envVars[existingIndex].type !== 'DroneAddr'
      ) {
        throw new Error('请选择类型为“设备-无人机”的环境变量');
      }
      envVars[existingIndex] = {
        ...envVars[existingIndex],
        defaultValue: connectionUri,
        value: connectionUri
      };
    } else {
      envVars.push({
        type: 'Drone',
        name: envVarName,
        required: false,
        description: '设备-无人机',
        defaultValue: connectionUri,
        sensitive: false,
        options: [],
        value: connectionUri
      });
    }
    await api.saveApplicationSettings(buildAppPayload({ envVars }));
    await refreshContext();
  }

  function openDeviceEditor(device, source) {
    state.deviceDraft = device
      ? deviceToDraft(device)
      : defaultDeviceDraft(source);
    state.deviceEditorOpen = true;
    state.activeTab = 'devices';
    render();
  }

  async function saveDeviceInline() {
    if (!api.saveDroneDevice) {
      toast('当前设备保存接口未接入');
      return;
    }
    if (!String(state.deviceDraft?.name || '').trim()) {
      toast('请填写设备名称');
      return;
    }
    if (
      (state.deviceDraft?.source === 'real' ||
        state.deviceDraft?.px4StartMode === 'manual-connect') &&
      !String(state.deviceDraft?.connectionUri || '').trim()
    ) {
      toast('请填写连接地址');
      return;
    }
    state.savingDevice = true;
    render();
    try {
      const devicePayload = buildDevicePayload();
      await api.saveDroneDevice(devicePayload);
      state.deviceEditorOpen = false;
      state.deviceDraft = null;
      await refreshDevices(false);
      await refreshContext();
      toast('无人机已保存到全局设备库');
    } catch (error) {
      toast(String(error && error.message ? error.message : error));
    } finally {
      state.savingDevice = false;
      render();
    }
  }

  async function deleteDeviceInline(deviceId) {
    if (!api.deleteDroneDevice || !deviceId) {
      toast('当前设备删除接口未接入');
      return;
    }
    if (!window.confirm('确认删除这台设备吗？')) {
      return;
    }
    try {
      const removed = await api.deleteDroneDevice(deviceId);
      if (!removed) {
        toast('未找到要删除的设备，请刷新后重试');
        await refreshDevices(false);
        return;
      }
      // [UAV-IDE MODIFY] Optimistically remove the deleted device before the async refresh finishes.
      state.devices = state.devices.filter(device => device.id !== deviceId);
      syncSimulationSelection();
      render();
      await refreshDevices(false);
      await refreshContext();
      toast('设备已删除，并已同步环境变量');
    } catch (error) {
      toast(String(error && error.message ? error.message : error));
    }
  }

  function selectedSimulationDevices() {
    const ids = new Set(state.selectedSimulationDeviceIds);
    return getSimulationDevices().filter(device => ids.has(device.id));
  }

  function prepareEmbeddedHudForSimulationDevices(devices) {
    const hudDevices = ensureArray(devices);
    if (!hudDevices.length) {
      return;
    }
    state.hudPinnedDeviceIds = hudDevices
      .map(device => String(device?.id || '').trim())
      .filter(Boolean);
    state.hudClosedDeviceIds = [];
    state.hudSnapshot = null;
    state.hudOpen = false;
    state.simulationCommandPending = 0;
    state.simulationCommandResult = null;
    state.simulationDiagnostics = null;
    state.simulationWeatherOpen = false;
    state.simulationWeatherPreset = 'clear';
    state.activeTab = 'simulation';
  }

  // [UAV-IDE MODIFY] Keep scene selection separate from device selection so future PX4/Project AirSim orchestration can evolve independently.
  function selectedSimulationScene() {
    return state.simulationScenes.find(
      scene => scene.id === state.selectedSimulationSceneId
    );
  }

  function defaultSceneDraft(scene) {
    return {
      id: scene?.id || '',
      name: scene?.name || '',
      rootDirectory: scene?.rootDirectory || '',
      executablePath: scene?.executablePath || ''
    };
  }

  function sceneNameFromPath(filePath) {
    const text = String(filePath || '').trim();
    const fileName = text.split(/[\\/]/).pop() || '';
    return fileName.replace(/\.exe$/i, '') || 'Project AirSim 场景';
  }

  function applySceneState(result) {
    invalidateSimulationEnvironment();
    state.simulationScenes = ensureArray(result?.scenes);
    state.selectedSimulationSceneId = result?.selectedSceneId || '';
    state.simulationConfigDraft = {
      px4Path: result?.px4Path || state.simulationConfigDraft.px4Path || '',
      scenePackageDirectory:
        result?.scenePackageDirectory ||
        state.simulationConfigDraft.scenePackageDirectory ||
        ''
    };
  }

  function openSceneManager() {
    state.sceneManagerOpen = true;
    state.simulationConfigDraft = {
      px4Path: state.simulationConfigDraft.px4Path || '',
      scenePackageDirectory:
        state.simulationConfigDraft.scenePackageDirectory || ''
    };
    state.sceneManagerSelectedId =
      state.selectedSimulationSceneId || state.simulationScenes[0]?.id || '';
    state.sceneDraft = defaultSceneDraft();
    render();
    void refreshSimulationScenes(false);
  }

  async function selectSimulationScene(sceneId) {
    invalidateSimulationEnvironment();
    state.selectedSimulationSceneId = sceneId || '';
    render();
    if (!api.selectSimulationScene) {
      toast('当前场景选择接口未接入');
      return;
    }
    try {
      applySceneState(await api.selectSimulationScene(sceneId || ''));
      render();
      void refreshSimulationEnvironment(false);
    } catch (error) {
      toast(String(error && error.message ? error.message : error));
      await refreshSimulationScenes(false);
    }
  }

  async function browseSceneExecutable() {
    if (!api.addUavSimulationSceneDirectory) {
      toast('当前场景目录选择接口未接入');
      return;
    }
    const previousIds = new Set(state.simulationScenes.map(scene => scene.id));
    try {
      const result = await api.addUavSimulationSceneDirectory();
      if (result && result.scenes) {
        applySceneState(result);
        const addedScene = state.simulationScenes.find(
          scene => !previousIds.has(scene.id)
        );
        state.sceneManagerSelectedId =
          addedScene?.id ||
          state.selectedSimulationSceneId ||
          state.sceneManagerSelectedId;
        state.sceneDraft = defaultSceneDraft();
        toast('场景目录已添加');
        void refreshSimulationEnvironment(false);
      }
      render();
    } catch (error) {
      toast(String(error && error.message ? error.message : error));
    }
  }
  async function saveSimulationConfigInline() {
    if (!api.saveUavSimulationConfig) {
      toast('当前仿真配置保存接口未接入');
      return;
    }
    try {
      applySceneState(
        await api.saveUavSimulationConfig(state.simulationConfigDraft)
      );
      toast('仿真配置已保存');
      render();
      void refreshSimulationEnvironment(false);
    } catch (error) {
      toast(String(error && error.message ? error.message : error));
    }
  }

  async function browseSimulationConfigDirectory(field) {
    if (field !== 'px4Path' && field !== 'scenePackageDirectory') {
      return;
    }
    if (!api.selectUavSimulationDirectory) {
      toast('当前版本不支持选择仿真配置目录');
      return;
    }
    try {
      const selectedPath = await api.selectUavSimulationDirectory({
        field,
        currentPath: state.simulationConfigDraft[field] || ''
      });
      if (selectedPath) {
        state.simulationConfigDraft[field] = selectedPath;
        render();
      }
    } catch (error) {
      toast(String(error && error.message ? error.message : error));
    }
  }

  async function saveSceneInline() {
    const draft = state.sceneDraft || defaultSceneDraft();
    if (!draft.id) {
      await browseSceneExecutable();
      return;
    }
    if (!String(draft.name || '').trim()) {
      toast('请填写场景名称');
      return;
    }
    if (!api.updateUavSimulationScene) {
      toast('当前场景保存接口未接入');
      return;
    }
    state.savingScene = true;
    render();
    try {
      applySceneState(
        await api.updateUavSimulationScene({ id: draft.id, name: draft.name })
      );
      state.sceneDraft = defaultSceneDraft();
      toast('场景名称已更新');
      void refreshSimulationEnvironment(false);
    } catch (error) {
      toast(String(error && error.message ? error.message : error));
    } finally {
      state.savingScene = false;
      render();
    }
  }
  async function deleteSceneInline(sceneId) {
    if (!api.deleteUavSimulationScene || !sceneId) {
      toast('当前场景删除接口未接入');
      return;
    }
    if (!window.confirm('确认删除这个场景吗？')) {
      return;
    }
    try {
      applySceneState(await api.deleteUavSimulationScene(sceneId));
      state.sceneManagerSelectedId =
        state.selectedSimulationSceneId || state.simulationScenes[0]?.id || '';
      if (state.sceneDraft?.id === sceneId) {
        state.sceneDraft = defaultSceneDraft();
      }
      toast('场景已删除');
      render();
      void refreshSimulationEnvironment(false);
    } catch (error) {
      toast(String(error && error.message ? error.message : error));
    }
  }

  async function startSimulation() {
    if (isSimulationStartLocked()) {
      toast(
        currentSimulationLifecycleState() === 'running'
          ? '\u4eff\u771f\u5df2\u5728\u8fd0\u884c'
          : '\u4eff\u771f\u6b63\u5728\u542f\u52a8\u6216\u505c\u6b62'
      );
      return;
    }
    if (!ensureSimulationEnvironmentReady()) {
      return;
    }
    state.startingSimulation = true;
    state.simulationStatus = {
      ...(state.simulationStatus || {}),
      state: 'checking',
      title: '\u6b63\u5728\u542f\u52a8\u4eff\u771f',
      message:
        '\u6b63\u5728\u63d0\u4ea4\u4eff\u771f\u542f\u52a8\u8bf7\u6c42...',
      stages: state.simulationStatus?.stages || []
    };
    render();
    scheduleSimulationStatusPolling();
    // [UAV-IDE MODIFY] Send selected device templates only; the local simulation service now owns per-run runtime planning.
    const drones = selectedSimulationDevices();
    if (!drones.length) {
      state.startingSimulation = false;
      state.simulationStatus = null;
      toast('请先选择至少一台设备');
      render();
      scheduleSimulationStatusPolling();
      return;
    }
    const scene = selectedSimulationScene();
    if (!scene) {
      state.startingSimulation = false;
      state.simulationResult = {
        state: 'FAILED',
        title: 'FAILED - 本地仿真启动失败',
        message: '请先选择 Project AirSim 场景'
      };
      state.simulationStatus = {
        state: 'failed',
        title: state.simulationResult.title,
        message: state.simulationResult.message,
        stages: []
      };
      state.simulationLogs = appendSimulationLog(
        state.simulationLogs,
        '请先选择 Project AirSim 场景'
      );
      toast('请先选择 Project AirSim 场景');
      render();
      scheduleSimulationStatusPolling();
      return;
    }
    // [UAV-IDE MODIFY] Prepare the sidebar HUD list without opening standalone BrowserWindows.
    prepareEmbeddedHudForSimulationDevices(drones);
    // [UAV-IDE MODIFY] Do not show fake port/connect logs; the local simulation service now returns real stage status.
    state.simulationLogs = [
      {
        time: new Date().toTimeString().slice(0, 8),
        message: '已提交本地仿真启动请求'
      }
    ];
    state.simulationResult = {
      title: 'SITL 启动中',
      message: '正在提交仿真启动请求...'
    };
    render();
    const simulationStartRequestedAt = performance.now();
    try {
      state.simulationResult = api.startUavSimulation
        ? await api.startUavSimulation({
            // [UAV-IDE MODIFY] Send the selected scene registration and devices to the isolated UAV simulation subsystem.
            sceneId: scene.id,
            drones
          })
        : {
            title: '仿真入口不可用',
            message: '当前 preload 未暴露仿真启动能力。'
          };
      // [UAV-IDE MODIFY] Surface structured local simulation failure state to legacy status parsing.
      if (
        state.simulationResult?.state === 'FAILED' &&
        state.simulationResult.title
      ) {
        state.simulationResult.title =
          'FAILED - ' + state.simulationResult.title;
      }
      state.simulationStatus = {
        ...(state.simulationStatus || {}),
        state:
          normalizeSimulationState(state.simulationResult?.state) === 'running'
            ? 'running'
            : normalizeSimulationState(state.simulationResult?.state),
        title:
          state.simulationResult?.title || state.simulationStatus?.title || '',
        message:
          state.simulationResult?.message ||
          state.simulationStatus?.message ||
          '',
        stages:
          state.simulationResult?.stages ||
          state.simulationStatus?.stages ||
          [],
        drones:
          state.simulationResult?.drones || state.simulationStatus?.drones || []
      };
      state.simulationLogs = appendSimulationLog(
        state.simulationLogs,
        state.simulationResult?.title || '仿真启动请求已完成'
      );
      syncEmbeddedHudStateFromSimulation();
      scheduleHudPolling();
      toast('仿真启动请求已完成');
    } catch (error) {
      state.simulationResult = {
        title: '仿真启动失败',
        message: String(error && error.message ? error.message : error)
      };
      state.simulationLogs = appendSimulationLog(
        state.simulationLogs,
        state.simulationResult.message
      );
      toast('仿真启动失败');
    } finally {
      state.simulationLogs = appendSimulationLog(
        state.simulationLogs,
        '\u4eff\u771f\u542f\u52a8\u8bf7\u6c42\u603b\u8017\u65f6 ' +
          Math.max(
            0,
            Math.round(performance.now() - simulationStartRequestedAt)
          ) +
          ' ms'
      );
      state.startingSimulation = false;
      syncEmbeddedHudStateFromSimulation();
      scheduleHudPolling();
      void refreshUavSimulationStatus(false);
      scheduleSimulationStatusPolling();
      render();
    }
  }

  // [UAV-IDE MODIFY] Stop only the processes owned by the independent UAV simulation supervisor.
  async function stopSimulation() {
    if (!api.stopUavSimulation) {
      toast('当前仿真停止接口未接入');
      return;
    }
    if (state.stoppingSimulation) {
      return;
    }
    state.stoppingSimulation = true;
    state.simulationStatus = {
      ...(state.simulationStatus || {}),
      state: 'stopping',
      title: '\u6b63\u5728\u505c\u6b62\u4eff\u771f',
      message: '\u6b63\u5728\u5173\u95ed\u4eff\u771f\u8d44\u6e90...',
      stages: state.simulationStatus?.stages || []
    };
    render();
    scheduleSimulationStatusPolling();
    const simulationStopRequestedAt = performance.now();
    try {
      state.simulationStatus = await api.stopUavSimulation();
      state.simulationResult = {
        state: state.simulationStatus?.state || 'idle',
        title: state.simulationStatus?.title || '仿真已停止',
        message: state.simulationStatus?.message || '本次仿真资源已关闭。',
        stages: state.simulationStatus?.stages || []
      };
      state.simulationLogs = appendSimulationLog(
        state.simulationLogs,
        state.simulationResult.title
      );
      syncEmbeddedHudStateFromSimulation();
      toast(
        state.simulationStatus?.state === 'idle'
          ? '\u4eff\u771f\u5df2\u505c\u6b62'
          : '\u5df2\u53d6\u6d88\u505c\u6b62\u4eff\u771f'
      );
    } catch (error) {
      toast(String(error && error.message ? error.message : error));
    } finally {
      state.simulationLogs = appendSimulationLog(
        state.simulationLogs,
        (state.simulationStatus?.state === 'idle'
          ? '\u4eff\u771f\u505c\u6b62\u603b\u8017\u65f6 '
          : '\u505c\u6b62\u8bf7\u6c42\u8017\u65f6 ') +
          Math.max(
            0,
            Math.round(performance.now() - simulationStopRequestedAt)
          ) +
          ' ms'
      );
      syncEmbeddedHudStateFromSimulation();
      scheduleHudPolling();
      scheduleSimulationStatusPolling();
      state.stoppingSimulation = false;
      render();
    }
  }

  function withSimulationRuntime(device, index) {
    const port = getTelemetryPort(device, 14550 + index);
    const systemId = Number(device.systemId || index + 1);
    const connectionUri = 'udp://0.0.0.0:' + port;
    return {
      ...device,
      telemetry: {
        ...(device.telemetry || {}),
        protocol: 'udp',
        mode: 'listen',
        bindHost: '0.0.0.0',
        bindPort: Number(port)
      },
      systemId,
      simulation: {
        ...(device.simulation || {}),
        runtime: {
          ...(device.simulation?.runtime || {}),
          state: 'starting',
          connectionUri,
          systemId,
          mavlinkPort: Number(port),
          px4Instance: index
        }
      }
    };
  }

  // [UAV-IDE MODIFY] Deprecated fake startup log generator retained only to avoid broad text-encoding churn in this legacy asset.
  function deprecatedBuildInitialSimulationLogs(drones) {
    const selected = drones.length ? drones : selectedSimulationDevices();
    const lines = [
      '初始化仿真环境...',
      '检查端口 14550 ... OK',
      '检查端口 14551 ... OK'
    ];
    selected.forEach((device, index) => {
      lines.push(
        '启动 PX4 SITL (' +
          getDeviceName(device, index) +
          ', 端口 ' +
          getTelemetryPort(device, 14550 + index) +
          ')'
      );
    });
    selected.forEach((device, index) => {
      lines.push('连接至 ' + getDeviceName(device, index) + ' ... OK');
    });
    lines.push(
      'SITL 环境启动中 (' +
        selected.length +
        '/' +
        Math.max(selected.length, 1) +
        ')...'
    );
    return lines.map((message, index) => ({
      time: '16:25:' + String(18 + index).padStart(2, '0'),
      message
    }));
  }

  function appendSimulationLog(logs, message) {
    return logs.concat({
      time: new Date().toTimeString().slice(0, 8),
      message
    });
  }

  // [UAV-IDE MODIFY] Render local simulation service stages so startup failures point to the exact pipeline step.
  function renderSimulationStages(result) {
    const stages = ensureArray(result?.stages);
    if (!stages.length) {
      return '';
    }
    return (
      '<div class="jld-skylab-sim-stage-list">' +
      stages
        .map(stage => {
          return (
            '<div class="jld-skylab-sim-stage" data-state="' +
            esc(stage.state || '') +
            '"><span>' +
            esc(stage.state || '') +
            '</span><strong>' +
            esc(stage.stage || '') +
            '</strong><small>' +
            esc(stage.detail || stage.message || '') +
            '</small></div>'
          );
        })
        .join('') +
      '</div>'
    );
  }

  function railIcon(name) {
    const icons = {
      app:
        '<svg class="jld-skylab-rail-icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="5"></rect><path d="M8 9.5 12 7l4 2.5v5L12 17l-4-2.5z"></path><path d="M10 10.7h4"></path><path d="M10 13.3h4"></path></svg>',
      devices:
        '<svg class="jld-skylab-rail-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 9h8l2 8H6z"></path><path d="M9.5 9 11 5h2l1.5 4"></path><path d="M8 17v2"></path><path d="M16 17v2"></path><path d="M9 13h.01"></path><path d="M15 13h.01"></path></svg>',
      simulation:
        '<svg class="jld-skylab-rail-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="2"></circle><path d="M12 4v6"></path><path d="M12 14v6"></path><path d="M4 12h6"></path><path d="M14 12h6"></path><path d="M7.8 7.8 10.6 10.6"></path><path d="M13.4 13.4l2.8 2.8"></path><path d="M16.2 7.8 13.4 10.6"></path><path d="M10.6 13.4l-2.8 2.8"></path></svg>',
      groundstation:
        '<svg class="jld-skylab-rail-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 19h14"></path><path d="M8 19l2-7h4l2 7"></path><path d="M12 12V7"></path><path d="M9 8.5a4 4 0 0 1 6 0"></path><path d="M6.5 6a7.5 7.5 0 0 1 11 0"></path></svg>',
      publish:
        '<svg class="jld-skylab-rail-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 17H7a4 4 0 1 1 .8-7.9A5 5 0 0 1 17 10a3.5 3.5 0 1 1 .5 7H16"></path><path d="M12 16V9"></path><path d="M9.5 11.5 12 9l2.5 2.5"></path></svg>'
    };
    return icons[name] || icons.app;
  }

  // [UAV-IDE MODIFY] Give each device category a compact semantic icon in command-center cards.
  function deviceCategoryIcon(category) {
    const icons = {
      uav:
        '<svg class="jld-skylab-device-type-icon-svg" viewBox="0 0 32 32" aria-hidden="true"><rect x="12" y="12" width="8" height="8" rx="2"></rect><path d="m12 14-5-4M20 14l5-4M12 18l-5 4M20 18l5 4"></path><circle cx="6" cy="9" r="3"></circle><circle cx="26" cy="9" r="3"></circle><circle cx="6" cy="23" r="3"></circle><circle cx="26" cy="23" r="3"></circle><path d="M16 12V8M14 8h4"></path></svg>',
      ugv:
        '<svg class="jld-skylab-device-type-icon-svg" viewBox="0 0 32 32" aria-hidden="true"><path d="M6 19h20l-2-6a3 3 0 0 0-2.8-2H11a3 3 0 0 0-2.8 2L6 19Z"></path><path d="M4 19h24v4H4z"></path><circle cx="9" cy="24" r="2.5"></circle><circle cx="23" cy="24" r="2.5"></circle><path d="M11 15h10"></path></svg>',
      usv:
        '<svg class="jld-skylab-device-type-icon-svg" viewBox="0 0 32 32" aria-hidden="true"><path d="M7 18h18l-3 6H10l-3-6Z"></path><path d="M12 18v-7h8l3 7M16 11V7"></path><path d="M5 27c2 1.3 4 1.3 6 0 2 1.3 4 1.3 6 0 2 1.3 4 1.3 6 0 1.3.9 2.7 1.1 4 .6"></path></svg>',
      humanoid:
        '<svg class="jld-skylab-device-type-icon-svg" viewBox="0 0 32 32" aria-hidden="true"><circle cx="16" cy="7" r="3"></circle><path d="M12 13h8l1 8h-3l-1 7h-2l-1-7h-3l1-8Z"></path><path d="m12 14-5 6M20 14l5 6"></path></svg>',
      quadruped:
        '<svg class="jld-skylab-device-type-icon-svg" viewBox="0 0 32 32" aria-hidden="true"><rect x="8" y="11" width="15" height="10" rx="3"></rect><path d="M23 13h3l2 3-2 3h-3M8 13 5 10M11 21v6M20 21v6M9 27h4M18 27h4"></path><circle cx="25" cy="15" r=".8" fill="currentColor" stroke="none"></circle></svg>'
    };
    return icons[category] || icons.uav;
  }

  function renderRailButton(tab, label, icon) {
    return (
      '<button class="jld-skylab-rail-btn" title="' +
      esc(label) +
      '" aria-label="' +
      esc(label) +
      '" data-action="open-tab" data-tab="' +
      esc(tab) +
      '" data-active="' +
      (state.activeTab === tab && state.open ? '1' : '0') +
      '">' +
      icon +
      '</button>'
    );
  }

  function renderTabs() {
    const tabs = [
      ['app', '应用'],
      ['devices', '设备'],
      ['simulation', '仿真'],
      ['groundstation', '地面站']
    ];
    if (PUBLISH_UI_ENABLED) {
      tabs.push(['publish', '发布']);
    }
    return tabs
      .map(
        tab =>
          '<button class="jld-skylab-tab" data-action="switch-tab" data-tab="' +
          tab[0] +
          '" data-active="' +
          (state.activeTab === tab[0] ? '1' : '0') +
          '">' +
          tab[1] +
          '</button>'
      )
      .join('');
  }

  function renderOverviewCard(title, icon, meta, content) {
    return (
      '<div class="jld-skylab-overview-card"><div class="jld-skylab-overview-card-head"><div class="jld-skylab-overview-title">' +
      icon +
      '<strong>' +
      esc(title) +
      '</strong></div>' +
      meta +
      '</div>' +
      content +
      '</div>'
    );
  }

  function renderOverviewField(label, value) {
    return (
      '<div class="jld-skylab-overview-field"><span>' +
      esc(label) +
      '</span><strong>' +
      esc(value) +
      '</strong></div>'
    );
  }

  function renderAppPanel() {
    const ctx = normalizeContext(state.context);
    const currentDevice = state.devices[0];
    const simulationStatus = simulationOverviewStatus();
    return (
      renderOverviewCard(
        '应用',
        railIcon('app'),
        '<span class="jld-skylab-chevron">&rsaquo;</span>',
        '<div class="jld-skylab-overview-fields">' +
          renderOverviewField('appCode', ctx.appCode || '未配置') +
          renderOverviewField('版本', ctx.version || '未配置') +
          renderOverviewField('入口文件', ctx.entryFile || 'main.py') +
          renderOverviewField('环境变量', getEnvCount() + ' 个') +
          '</div>' +
          '<div class="jld-skylab-overview-actions">' +
          '<button class="jld-skylab-btn jld-skylab-btn-icon" data-action="toggle-app-edit">' +
          railIcon('app') +
          '<span>' +
          (state.appEditing ? '收起编辑' : '编辑基础信息') +
          '</span></button>' +
          (PUBLISH_UI_ENABLED
            ? '<button class="jld-skylab-btn jld-skylab-btn-icon" data-variant="primary" data-action="open-publish">' +
              railIcon('publish') +
              '<span>打开发布向导</span></button>'
            : '') +
          '</div>' +
          (state.appEditing ? renderAppQuickForm() : '')
      ) +
      renderOverviewCard(
        '设备',
        railIcon('devices'),
        '<div class="jld-skylab-device-count"><strong>' +
          state.devices.length +
          '</strong><span>已配置</span></div>',
        currentDevice
          ? '<div class="jld-skylab-current-device"><div><span>当前设备</span><strong>' +
              esc(currentDevice.name || '未命名设备') +
              ' (' +
              esc(
                currentDevice.accessType === 'physical'
                  ? '真实设备'
                  : 'PX4 SITL'
              ) +
              ')</strong><small>' +
              esc(getDeviceSummaryText(currentDevice)) +
              '</small></div><button class="jld-skylab-btn" data-action="switch-tab" data-tab="devices">设备页</button></div>'
          : '<div class="jld-skylab-current-device"><div><span>当前设备</span><strong>还没有设备</strong><small>在侧栏设备页添加后，可按需同步为环境变量</small></div><button class="jld-skylab-btn" data-action="new-device">添加设备</button></div>'
      ) +
      renderOverviewCard(
        '仿真',
        railIcon('simulation'),
        '<span class="jld-skylab-chevron">&rsaquo;</span>',
        '<div class="jld-skylab-sim-status"><span>状态</span><strong>' +
          esc(simulationStatus) +
          '</strong></div><button class="jld-skylab-btn jld-skylab-wide-primary" data-variant="primary" data-action="start-simulation"' +
          simulationStartButtonAttrs() +
          '>' +
          simulationStartButtonLabel() +
          '</button>'
      )
    );
  }

  function renderAppQuickForm() {
    return (
      '<div class="jld-skylab-inline-editor">' +
      renderField(
        'appCode',
        '应用标识',
        state.appDraft.appCode || '',
        'fire-guard'
      ) +
      renderField('version', '版本', state.appDraft.version || '', '1.0.0') +
      renderField(
        'entryFile',
        '入口文件',
        state.appDraft.entryFile || 'main.py',
        'main.py'
      ) +
      renderField(
        'maxRunSeconds',
        '最大运行秒数',
        state.appDraft.maxRunSeconds || 300,
        '300',
        'number'
      ) +
      '<div class="jld-skylab-inline-actions"><button class="jld-skylab-btn" data-action="advanced-app-settings">高级应用设置</button><button class="jld-skylab-btn" data-variant="primary" data-action="save-app-quick">' +
      (state.savingApp ? '保存中...' : '保存基础信息') +
      '</button></div>' +
      '<div class="jld-skylab-inline-note">环境变量、AI 网关、地面站等完整配置仍在高级应用设置中维护。</div>' +
      '</div>'
    );
  }

  function renderField(name, label, value, placeholder, type) {
    return (
      '<label class="jld-skylab-field"><span>' +
      esc(label) +
      '</span><input data-field="' +
      esc(name) +
      '" data-scope="app" type="' +
      esc(type || 'text') +
      '" value="' +
      esc(value) +
      '" placeholder="' +
      esc(placeholder || '') +
      '"></label>'
    );
  }

  function renderDevicesPanel() {
    // [UAV-IDE MODIFY] Keep CRUD in the standalone device library while sidebar cards deep-link to device details.
    return renderDeviceLibraryEntry() + renderDeviceList();
  }

  function renderDeviceLibraryEntry() {
    return (
      '<button type="button" class="jld-skylab-device-library-entry" data-action="open-device-manager" aria-label="\u6253\u5f00\u8bbe\u5907\u5e93\uff0c\u6dfb\u52a0\u3001\u7f16\u8f91\u6216\u5220\u9664\u8bbe\u5907">' +
      '<span class="jld-skylab-device-library-icon" aria-hidden="true"><svg viewBox="0 0 32 32"><rect x="5" y="7" width="22" height="18" rx="5"></rect><path d="M10 13h12M10 18h7"></path><circle cx="23" cy="21" r="5"></circle><path d="M23 18.5v5M20.5 21h5"></path></svg></span>' +
      '<span class="jld-skylab-device-library-copy"><strong>\u8bbe\u5907\u7ba1\u7406</strong><small>\u6dfb\u52a0\u3001\u7f16\u8f91\u3001\u5220\u9664\u53ca\u914d\u7f6e\u4f20\u611f\u5668</small></span>' +
      '<span class="jld-skylab-device-library-meta"><span>' +
      state.devices.length +
      ' \u53f0</span><b aria-hidden="true">&rsaquo;</b></span></button>'
    );
  }

  function renderDeviceEditor() {
    const draft = state.deviceDraft || defaultDeviceDraft();
    const runtimeKind =
      draft.runtimeKind ||
      (draft.source === 'real' ? 'physical' : 'simulation');
    const isReal = runtimeKind === 'physical' || draft.source === 'real';
    const category = draft.deviceCategory || 'uav';
    const startMode = isReal
      ? 'manual-connect'
      : draft.px4StartMode || 'ide-managed';
    const manual = startMode === 'manual-connect';
    const categoryOptions = Object.keys(DEVICE_CATEGORY_LABELS)
      .map(
        key =>
          '<option value="' +
          esc(key) +
          '" ' +
          (category === key ? 'selected' : '') +
          '>' +
          esc(DEVICE_CATEGORY_LABELS[key]) +
          '</option>'
      )
      .join('');
    const runtimeOptions = Object.keys(RUNTIME_KIND_LABELS)
      .map(
        key =>
          '<option value="' +
          esc(key) +
          '" ' +
          (runtimeKind === key ? 'selected' : '') +
          '>' +
          esc(RUNTIME_KIND_LABELS[key]) +
          '</option>'
      )
      .join('');
    const airframeOptions = Object.keys(UAV_AIRFRAME_PROFILES)
      .map(
        key =>
          '<option value="' +
          esc(key) +
          '" ' +
          ((draft.airframeType || 'quadrotor') === key ? 'selected' : '') +
          '>' +
          esc(UAV_AIRFRAME_PROFILES[key].label) +
          '</option>'
      )
      .join('');
    return (
      '<div class="jld-skylab-section jld-skylab-inline-editor">' +
      '<div class="jld-skylab-section-title">' +
      (draft.id ? '\u7f16\u8f91\u8bbe\u5907' : '\u6dfb\u52a0\u8bbe\u5907') +
      '</div>' +
      '<label class="jld-skylab-check-field"><input type="checkbox" data-scope="device" data-field="enabled" ' +
      (draft.enabled !== false ? 'checked' : '') +
      '><span>\u542f\u7528\u8bbe\u5907</span></label>' +
      '<label class="jld-skylab-field"><span>\u8bbe\u5907\u7c7b\u578b</span><select data-scope="device" data-field="deviceCategory">' +
      categoryOptions +
      '</select></label>' +
      '<label class="jld-skylab-field"><span>\u8bbe\u5907\u6765\u6e90</span><select data-scope="device" data-field="runtimeKind">' +
      runtimeOptions +
      '</select></label>' +
      renderDeviceField(
        'name',
        '\u540d\u79f0',
        draft.name,
        '\u8bbe\u5907\u540d\u79f0'
      ) +
      renderDeviceField(
        'description',
        '\u5907\u6ce8',
        draft.description || '',
        '\u53ef\u9009'
      ) +
      (category === 'uav'
        ? '<label class="jld-skylab-field"><span>\u65e0\u4eba\u673a\u673a\u578b</span><select data-scope="device" data-field="airframeType">' +
          airframeOptions +
          '</select></label>' +
          renderDeviceField(
            'airsimVehicleName',
            'Project AirSim \u540d\u79f0',
            draft.airsimVehicleName,
            'Drone1'
          )
        : '<div class="jld-skylab-inline-note">\u5f53\u524d Project AirSim + PX4 \u542f\u52a8\u94fe\u8def\u53ea\u652f\u6301\u65e0\u4eba\u673a\u8bbe\u5907\u3002\u8be5\u8bbe\u5907\u53ef\u4fdd\u5b58\u5230\u5168\u5c40\u5e93\uff0c\u4f46\u4e0d\u4f1a\u51fa\u73b0\u5728\u4eff\u771f\u542f\u52a8\u5217\u8868\u3002</div>') +
      renderDeviceField(
        'systemId',
        'System ID',
        draft.systemId,
        '1',
        'number'
      ) +
      '<label class="jld-skylab-field"><span>PX4 \u542f\u52a8\u65b9\u5f0f</span><select data-scope="device" data-field="px4StartMode" ' +
      (isReal ? 'disabled' : '') +
      '><option value="ide-managed" ' +
      (!manual ? 'selected' : '') +
      '>IDE \u7ba1\u7406\u542f\u52a8</option><option value="manual-connect" ' +
      (manual ? 'selected' : '') +
      '>\u624b\u52a8\u8fde\u63a5</option></select></label>' +
      (manual
        ? renderDeviceField(
            'connectionUri',
            '\u8fde\u63a5\u65b9\u5f0f',
            draft.connectionUri,
            'udp://0.0.0.0:14550'
          )
        : '') +
      renderDeviceEnvVarFields(draft) +
      '<div class="jld-skylab-inline-actions"><button class="jld-skylab-btn" data-action="cancel-device-edit">\u53d6\u6d88</button><button class="jld-skylab-btn" data-variant="primary" data-action="save-device-inline">' +
      (state.savingDevice ? '\u4fdd\u5b58\u4e2d...' : '\u4fdd\u5b58') +
      '</button></div></div>'
    );
  }

  // [UAV-IDE MODIFY] Env var sync is an explicit optional section in the add-device form.
  function renderDeviceEnvVarFields(draft) {
    return '<div class="jld-skylab-inline-note">设备不会写入应用环境变量；绑定后由 fleetdock_runtime.py 初始化为 FleetDock.devices.<别名>。</div>';
  }

  function renderSimulationDeviceFields(draft) {
    const manual = draft.px4StartMode === 'manual-connect';
    return (
      renderDeviceField(
        'model',
        '机型',
        draft.model || 'quadrotor',
        'quadrotor'
      ) +
      renderDeviceField(
        'airsimVehicleName',
        'AirSim Vehicle',
        draft.airsimVehicleName,
        'Drone1'
      ) +
      '<label class="jld-skylab-field"><span>PX4 启动方式</span><select data-scope="device" data-field="px4StartMode"><option value="ide-managed" ' +
      (!manual ? 'selected' : '') +
      '>IDE 管理启动</option><option value="manual-connect" ' +
      (manual ? 'selected' : '') +
      '>手动启动，只让 IDE 连接</option></select></label>' +
      '<div class="jld-skylab-inline-note">仿真无人机保存为模板，端口、System ID、PX4 实例默认在启动仿真时自动分配。</div>' +
      (manual
        ? renderDeviceField('host', '连接地址', draft.host, '0.0.0.0') +
          renderDeviceField('port', '连接端口', draft.port, '14550', 'number')
        : '')
    );
  }

  function renderRealDeviceFields(draft) {
    return (
      '<label class="jld-skylab-field"><span>连接方式</span><select data-scope="device" data-field="connectionKind"><option value="udp" ' +
      (draft.connectionKind !== 'tcp' && draft.connectionKind !== 'serial'
        ? 'selected'
        : '') +
      '>UDP</option><option value="tcp" ' +
      (draft.connectionKind === 'tcp' ? 'selected' : '') +
      '>TCP</option><option value="serial" ' +
      (draft.connectionKind === 'serial' ? 'selected' : '') +
      '>Serial</option></select></label>' +
      (draft.connectionKind === 'serial'
        ? renderDeviceField('serialPort', '串口', draft.serialPort, 'COM3') +
          renderDeviceField(
            'baudRate',
            '波特率',
            draft.baudRate,
            '57600',
            'number'
          )
        : renderDeviceField('host', '连接地址', draft.host, '0.0.0.0') +
          renderDeviceField('port', '连接端口', draft.port, '14550', 'number'))
    );
  }

  function renderDeviceField(name, label, value, placeholder, type) {
    return (
      '<label class="jld-skylab-field"><span>' +
      esc(label) +
      '</span><input data-field="' +
      esc(name) +
      '" data-scope="device" type="' +
      esc(type || 'text') +
      '" value="' +
      esc(value) +
      '" placeholder="' +
      esc(placeholder || '') +
      '"></label>'
    );
  }

  function renderDeviceList() {
    // [UAV-IDE MODIFY] Keep command-center cards focused on device identity, category, and source.
    if (state.loadingDevices) {
      return '<div class="jld-skylab-compact-empty">\u6b63\u5728\u52a0\u8f7d\u8bbe\u5907...</div>';
    }
    if (!state.devices.length) {
      return (
        '<div class="jld-skylab-guide">' +
        '<div class="jld-skylab-section-title">\u8fd8\u6ca1\u6709\u8bbe\u5907</div>' +
        '<p>\u901a\u8fc7\u4e0a\u65b9\u201c\u8bbe\u5907\u7ba1\u7406\u201d\u6dfb\u52a0\u7b2c\u4e00\u53f0\u8bbe\u5907\u3002\u5f53\u524d\u4eff\u771f\u542f\u52a8\u53ea\u4f7f\u7528\u65e0\u4eba\u673a\u4eff\u771f\u8bbe\u5907\u3002</p>' +
        '</div>'
      );
    }
    return (
      '<div class="jld-skylab-list">' +
      state.devices
        .map(device => {
          const category = device.deviceCategory || 'uav';
          const runtimeKind =
            device.runtimeKind ||
            (device.source === 'real' ? 'physical' : 'simulation');
          const isPhysical =
            runtimeKind === 'physical' || device.source === 'real';
          return (
            '<div class="jld-skylab-row jld-skylab-device-card-shell" data-has-debug="' +
            (isPhysical ? '1' : '0') +
            '"><button type="button" class="jld-skylab-device-card" data-action="open-device-manager" data-device-id="' +
            esc(device.id) +
            '" aria-label="\u67e5\u770b ' +
            esc(device.name || '\u672a\u547d\u540d\u8bbe\u5907') +
            ' \u7684\u8bbe\u5907\u8be6\u60c5"><span class="jld-skylab-device-card-main"><span class="jld-skylab-device-type-icon" data-category="' +
            esc(category) +
            '">' +
            deviceCategoryIcon(category) +
            '</span><span class="jld-skylab-device-card-content"><strong class="jld-skylab-device-card-name">' +
            esc(device.name || '\u672a\u547d\u540d\u8bbe\u5907') +
            '</strong><span class="jld-skylab-device-card-facts"><span>' +
            esc(DEVICE_CATEGORY_LABELS[category] || category) +
            '</span><span data-source="' +
            esc(runtimeKind) +
            '">' +
            esc(RUNTIME_KIND_LABELS[runtimeKind] || runtimeKind) +
            '</span></span></span></span><span class="jld-skylab-device-card-chevron" aria-hidden="true">&rsaquo;</span></button>' +
            (isPhysical
              ? '<button type="button" class="jld-skylab-device-debug-action" data-action="open-real-debugger" data-device-id="' +
                esc(device.id) +
                '" title="\u6253\u5f00\u771f\u673a\u8c03\u8bd5" aria-label="\u6253\u5f00 ' +
                esc(device.name || '\u672a\u547d\u540d\u8bbe\u5907') +
                ' \u7684\u771f\u673a\u8c03\u8bd5">\u8c03\u8bd5</button>'
              : '') +
            '</div>'
          );
        })
        .join('') +
      '</div>'
    );
  }

  function getSimulationStatusModel() {
    const status = state.simulationStatus || {};
    const result = state.simulationResult || {};
    const lifecycleState = currentSimulationLifecycleState();
    if (
      state.startingSimulation ||
      SIMULATION_TRANSIENT_STATES.has(lifecycleState)
    ) {
      const runningStage = ensureArray(status.stages || result.stages).find(
        item => item?.state === 'running'
      );
      return {
        title:
          status.title ||
          result.title ||
          runningStage?.message ||
          '\u4eff\u771f\u542f\u52a8\u4e2d',
        message:
          status.message ||
          result.message ||
          runningStage?.message ||
          '\u6b63\u5728\u5904\u7406\u4eff\u771f\u542f\u52a8\u6d41\u7a0b...',
        progress: simulationProgressForState(lifecycleState),
        elapsed: '--:--:--',
        remaining: '--:--:--',
        running: true
      };
    }
    if (lifecycleState === 'running') {
      return {
        title:
          status.title ||
          result.title ||
          '\u672c\u5730\u4eff\u771f\u5df2\u542f\u52a8',
        message:
          status.message ||
          result.message ||
          '\u4eff\u771f\u8d44\u6e90\u6b63\u5728\u8fd0\u884c\u3002',
        progress: 100,
        elapsed: '--:--:--',
        remaining: '00:00:00',
        running: false
      };
    }
    if (state.simulationResult) {
      const failed = /error|fail/i.test(
        state.simulationResult.title || state.simulationResult.message || ''
      );
      return {
        title: state.simulationResult.title || 'SITL 已完成',
        message:
          state.simulationResult.message ||
          state.simulationResult.detail ||
          '仿真启动请求已完成。',
        progress: failed ? 0 : 100,
        elapsed: failed ? '--:--:--' : '00:00:29',
        remaining: failed ? '--:--:--' : '00:00:00',
        running: false
      };
    }
    return {
      title: 'SITL 未启动',
      message: '选择设备并启动仿真环境。',
      progress: 0,
      elapsed: '--:--:--',
      remaining: '--:--:--',
      running: false
    };
  }

  function hudNumber(value, digits) {
    if (value === null || value === undefined || value === '') {
      return '--';
    }
    const number = Number(value);
    return Number.isFinite(number) ? number.toFixed(digits || 0) : '--';
  }

  function hudBool(value, yes, no) {
    if (value === true) {
      return yes;
    }
    if (value === false) {
      return no;
    }
    return '--';
  }

  function hudSpeed(device) {
    const velocity = device?.velocity || {};
    if (
      velocity.x === null ||
      velocity.x === undefined ||
      velocity.y === null ||
      velocity.y === undefined
    ) {
      return '--';
    }
    const x = Number(velocity.x);
    const y = Number(velocity.y);
    if (!Number.isFinite(x) || !Number.isFinite(y)) {
      return '--';
    }
    return Math.sqrt(x * x + y * y).toFixed(1);
  }

  function hudVerticalSpeed(device) {
    if (device?.velocity?.z === null || device?.velocity?.z === undefined) {
      return '--';
    }
    const z = Number(device?.velocity?.z);
    return Number.isFinite(z) ? z.toFixed(1) : '--';
  }

  function hudTelemetryAge(value) {
    if (value === null || value === undefined || value === '') {
      return '--';
    }
    const milliseconds = Number(value);
    if (!Number.isFinite(milliseconds)) {
      return '--';
    }
    if (milliseconds < 1000) {
      return Math.round(milliseconds) + ' ms';
    }
    return (milliseconds / 1000).toFixed(1) + ' s';
  }

  function hudBatteryPercent(value) {
    if (value === null || value === undefined || value === '') {
      return '--';
    }
    const number = Number(value);
    if (!Number.isFinite(number)) {
      return '--';
    }
    return (number <= 1 ? number * 100 : number).toFixed(0) + '%';
  }

  function hudFiniteNumber(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : 0;
  }

  function hudConfiguredDevice(hudDevice) {
    const deviceId = String(hudDevice?.deviceId || '');
    const alias = String(hudDevice?.alias || '');
    const name = String(hudDevice?.name || '');
    return state.devices.find(device => {
      return (
        String(device.id || '') === deviceId ||
        String(device.envVarName || '') === alias ||
        String(device.name || '') === name
      );
    });
  }

  function hudDeviceProfile(hudDevice) {
    const matched = hudConfiguredDevice(hudDevice);
    const airframe =
      matched?.uav?.airframeType ||
      matched?.simulation?.model ||
      hudDevice?.metadata?.airframeType ||
      '';
    if (airframe === 'hexarotor') {
      return 'hexarotor';
    }
    if (airframe === 'vtol_tailsitter' || airframe === 'vtol_tiltrotor') {
      return 'vtol';
    }
    return 'quadrotor';
  }

  function normalizeHudSensorType(value) {
    const type = String(value || '').toLowerCase();
    const aliases = {
      rgb_camera: 'camera',
      depth_camera: 'depth',
      gps: 'gnss',
      distance: 'distance-sensor',
      optical_flow: 'optical-flow',
      distance_sensor: 'distance-sensor'
    };
    return aliases[type] || type;
  }

  function hudSensorInventory(device) {
    const configured = hudConfiguredDevice(device);
    const sensors = [];
    ensureArray(configured?.sensors).forEach((sensor, index) => {
      const type = normalizeHudSensorType(sensor?.type);
      sensors.push({
        ...sensor,
        id: String(sensor?.id || 'configured:' + type + ':' + index),
        type,
        configured: true,
        runtime: false,
        discovered: false,
        subscribed: false,
        streamError: type === 'camera' ? '' : String(device?.sensorError || '')
      });
    });
    for (const sensor of ensureArray(device?.sensors)) {
      const id = String(sensor?.id || '');
      const type = normalizeHudSensorType(sensor?.type);
      const normalizedId = id.toLowerCase();
      const isPx4Proxy = normalizedId.startsWith('px4.');
      // [UAV-IDE MODIFY] Bind SDK descriptors to configured sensors by exact ID first, then by an
      // unoccupied kind slot so runtime IDs such as IMU1/GPS still activate their configured buttons.
      let existing =
        (normalizedId
          ? sensors.find(
              item => String(item?.id || '').toLowerCase() === normalizedId
            )
          : null) ||
        sensors.find(
          item =>
            item?.configured === true &&
            normalizeHudSensorType(item?.type) === type &&
            !item?.runtime
        );
      // [UAV-IDE MODIFY] Never let a disconnected PX4 proxy erase a same-kind Project AirSim sample.
      if (!existing && isPx4Proxy) {
        const sameType = sensors.find(
          item =>
            item?.configured === true &&
            normalizeHudSensorType(item?.type) === type
        );
        if (sameType?.runtime) {
          continue;
        }
        existing = sameType;
      }
      if (existing) {
        const configuredIdentity = existing.configured
          ? {
              id: existing.id,
              name: existing.name,
              type: existing.type,
              configured: true
            }
          : {};
        Object.assign(existing, sensor, {
          ...configuredIdentity,
          id: configuredIdentity.id || id,
          type,
          runtime: true,
          discovered: sensor?.discovered !== false,
          runtimeSensorId: id
        });
      } else {
        sensors.push({
          ...sensor,
          id,
          type,
          configured: false,
          runtime: true,
          discovered: sensor?.discovered !== false,
          enabled: true
        });
      }
    }
    return sensors.filter(sensor => sensor?.enabled !== false && sensor?.type);
  }

  function hudVehicleName(device) {
    const configured = hudConfiguredDevice(device);
    const statusDrone = ensureArray(state.simulationStatus?.drones).find(
      drone => {
        return (
          String(drone?.droneId || '') === String(device?.deviceId || '') ||
          String(drone?.vehicleName || '').toLowerCase() ===
            String(device?.metadata?.airsimVehicleName || '').toLowerCase()
        );
      }
    );
    return String(
      statusDrone?.vehicleName ||
        device?.metadata?.airsimVehicleName ||
        configured?.simulation?.airsimVehicleName ||
        configured?.uav?.vehicleName ||
        device?.name ||
        ''
    );
  }

  function hudWebRtcAvailable(device) {
    const vehicleName = hudVehicleName(device);
    return ensureArray(state.simulationStatus?.mediaService?.streams).some(
      stream => String(stream?.vehicleName) === vehicleName
    );
  }

  function hudRtspAvailable(device) {
    const vehicleName = hudVehicleName(device);
    return ensureArray(
      state.simulationStatus?.sensorService?.rtspVehicles
    ).some(candidate => String(candidate) === vehicleName);
  }

  function hudSensorState(sensor, device) {
    const type = normalizeHudSensorType(sensor?.type);
    if (sensor?.streamError) {
      return 'error';
    }
    if (type === 'camera') {
      if (hudRtspAvailable(device) && hudWebRtcAvailable(device)) {
        return 'live';
      }
      return sensor?.runtime || hudRtspAvailable(device)
        ? 'discovered'
        : 'configured';
    }
    if (!sensor?.runtime || sensor?.discovered === false) {
      return 'configured';
    }
    if (sensor?.stale) {
      return 'stale';
    }
    if (sensor?.sample) {
      return 'live';
    }
    return 'discovered';
  }

  function hudSensorStateLabel(value) {
    const labels = {
      configured: '\u5df2\u914d\u7f6e',
      discovered: 'SDK \u5df2\u53d1\u73b0',
      live: '\u5b9e\u65f6\u6570\u636e',
      stale: '\u6570\u636e\u8fc7\u671f',
      error: '\u8bfb\u53d6\u9519\u8bef'
    };
    return labels[value] || value;
  }

  function hudSensorLabel(type) {
    const labels = {
      camera: '\u76f8\u673a',
      depth: '\u6df1\u5ea6',
      lidar: 'LiDAR',
      imu: 'IMU',
      gnss: 'GNSS',
      barometer: '\u6c14\u538b\u8ba1',
      magnetometer: '\u78c1\u529b\u8ba1',
      airspeed: '\u7a7a\u901f\u8ba1',
      radar: '\u96f7\u8fbe',
      battery: '\u7535\u6c60',
      'distance-sensor': '\u6d4b\u8ddd',
      'optical-flow': '\u5149\u6d41'
    };
    return labels[type] || type;
  }

  function renderHudAttitudeLayer(device) {
    const attitude = device.attitude || {};
    const roll = hudFiniteNumber(attitude.roll);
    const pitch = hudFiniteNumber(attitude.pitch);
    const yaw = hudFiniteNumber(attitude.yaw);
    const profile = hudDeviceProfile(device);
    return (
      '<div class="jld-skylab-hud-attitude" data-connected="' +
      (device.connected ? '1' : '0') +
      '"><div class="jld-skylab-hud-horizon"><span></span><span></span></div>' +
      '<div class="jld-skylab-hud-model" data-profile="' +
      esc(profile) +
      '" style="--hud-roll:' +
      roll.toFixed(1) +
      'deg;--hud-pitch:' +
      pitch.toFixed(1) +
      'deg;--hud-yaw:' +
      yaw.toFixed(1) +
      'deg;">' +
      renderHudDroneShape(profile) +
      '</div><div class="jld-skylab-hud-angle-row"><span>R ' +
      hudNumber(attitude.roll, 0) +
      '\u00b0</span><span>P ' +
      hudNumber(attitude.pitch, 0) +
      '\u00b0</span><span>Y ' +
      hudNumber(attitude.yaw, 0) +
      '\u00b0</span></div></div>'
    );
  }

  function renderHudDroneShape(profile) {
    if (profile === 'hexarotor') {
      return (
        '<i class="hud-arm hud-arm-a"></i><i class="hud-arm hud-arm-b"></i><i class="hud-arm hud-arm-c"></i>' +
        '<b class="hud-body"></b><b class="hud-nose"></b>' +
        '<em class="hud-rotor r1"></em><em class="hud-rotor r2"></em><em class="hud-rotor r3"></em>' +
        '<em class="hud-rotor r4"></em><em class="hud-rotor r5"></em><em class="hud-rotor r6"></em>'
      );
    }
    if (profile === 'vtol') {
      return (
        '<i class="hud-wing"></i><i class="hud-tail"></i><b class="hud-body"></b><b class="hud-nose"></b>' +
        '<em class="hud-rotor r1"></em><em class="hud-rotor r2"></em><em class="hud-rotor r3"></em><em class="hud-rotor r4"></em>'
      );
    }
    return (
      '<i class="hud-arm hud-arm-a"></i><i class="hud-arm hud-arm-b"></i><b class="hud-body"></b><b class="hud-nose"></b>' +
      '<em class="hud-rotor r1"></em><em class="hud-rotor r2"></em><em class="hud-rotor r3"></em><em class="hud-rotor r4"></em>'
    );
  }

  function hudDeviceKey(device, index) {
    return String(
      device?.deviceId ||
        device?.id ||
        device?.alias ||
        device?.name ||
        'hud-' + index
    );
  }

  function hudDeviceMatchesSimulationDevice(hudDevice, device) {
    const values = [
      device?.id,
      device?.envVarName,
      device?.name,
      device?.simulation?.airsimVehicleName,
      device?.airsimVehicleName
    ]
      .map(value => String(value || '').toLowerCase())
      .filter(Boolean);
    return [hudDevice?.deviceId, hudDevice?.alias, hudDevice?.name]
      .map(value => String(value || '').toLowerCase())
      .some(value => value && values.includes(value));
  }

  function hudPlaceholderDevice(device, index) {
    return {
      deviceId: device?.id || 'hud-device-' + index,
      alias: device?.envVarName || device?.name || '',
      name: getDeviceName(device, index),
      connected: false,
      metadata: {
        airframeType:
          device?.uav?.airframeType ||
          device?.simulation?.model ||
          device?.airframeType ||
          ''
      }
    };
  }

  function hudDevicesForDisplay() {
    const snapshotDevices = ensureArray(state.hudSnapshot?.devices);
    const pinnedIds = state.hudPinnedDeviceIds.length
      ? state.hudPinnedDeviceIds
      : state.selectedSimulationDeviceIds;
    const simulationDevices = getSimulationDevices();
    const activeStatusDrones = ensureArray(state.simulationStatus?.drones);
    const pinnedDevices = pinnedIds.map((deviceId, index) => {
      const configuredDevice = simulationDevices.find(
        device => device.id === deviceId
      );
      if (configuredDevice) {
        return configuredDevice;
      }
      const runtimeDevice = activeStatusDrones.find(
        drone => String(drone?.droneId || '') === deviceId
      );
      const runtimeVehicleName =
        runtimeDevice?.vehicleName || runtimeDevice?.airsimVehicleName || '';
      return {
        id: deviceId,
        name: runtimeVehicleName || 'drone' + (index + 1),
        source: 'simulation',
        simulation: {
          airsimVehicleName: runtimeVehicleName
        }
      };
    });
    const usedSnapshotIndexes = new Set();
    const merged = pinnedDevices.map((device, index) => {
      const snapshotIndex = snapshotDevices.findIndex(
        (candidate, candidateIndex) => {
          return (
            !usedSnapshotIndexes.has(candidateIndex) &&
            hudDeviceMatchesSimulationDevice(candidate, device)
          );
        }
      );
      if (snapshotIndex < 0) {
        return hudPlaceholderDevice(device, index);
      }
      usedSnapshotIndexes.add(snapshotIndex);
      const snapshotDevice = snapshotDevices[snapshotIndex] || {};
      return {
        ...snapshotDevice,
        deviceId: snapshotDevice.deviceId || device.id,
        name: snapshotDevice.name || getDeviceName(device, index),
        metadata: {
          airframeType:
            device?.uav?.airframeType ||
            device?.simulation?.model ||
            snapshotDevice?.metadata?.airframeType ||
            '',
          ...(snapshotDevice.metadata || {})
        }
      };
    });
    // [UAV-IDE MODIFY] The embedded list only represents devices in the active managed run.
    return merged;
  }

  function renderHudStateStrip(device) {
    const failsafe = device?.failsafe;
    return (
      '<div class="jld-skylab-hud-state-strip">' +
      renderHudFlag(
        device?.flightMode || '\u672a\u77e5\u6a21\u5f0f',
        Boolean(device?.flightMode)
      ) +
      renderHudFlag(
        device?.armed === true
          ? 'ARM'
          : device?.armed === false
          ? 'LOCK'
          : 'ARM --',
        device?.armed
      ) +
      renderHudFlag(
        device?.inAir ? 'IN AIR' : 'GROUND',
        device?.inAir === true ? true : device?.inAir === false ? true : null
      ) +
      '<span data-ok="' +
      (failsafe === true ? '0' : failsafe === false ? '1' : '') +
      '">FAILSAFE ' +
      hudBool(failsafe, 'ON', 'OFF') +
      '</span><span class="jld-skylab-hud-age">AGE ' +
      esc(hudTelemetryAge(device?.telemetryAgeMs)) +
      '</span></div>'
    );
  }

  function renderHudPosition(device) {
    const global = device?.global || {};
    const local = device?.local || {};
    const home = device?.home || {};
    return (
      '<div class="jld-skylab-hud-position"><div class="jld-skylab-hud-vector">' +
      '<span><small>Local N</small><strong>' +
      hudNumber(local.x, 2) +
      ' m</strong></span>' +
      '<span><small>Local E</small><strong>' +
      hudNumber(local.y, 2) +
      ' m</strong></span>' +
      '<span><small>Local D</small><strong>' +
      hudNumber(local.z, 2) +
      ' m</strong></span>' +
      '</div><div class="jld-skylab-hud-geo"><span>GPS ' +
      hudNumber(global.lat, 6) +
      ', ' +
      hudNumber(global.lon, 6) +
      '</span><span>HOME ' +
      hudNumber(home.lat, 6) +
      ', ' +
      hudNumber(home.lon, 6) +
      '</span></div></div>'
    );
  }

  function renderHudSensorButtons(device, index) {
    const sensors = hudSensorInventory(device);
    if (!sensors.length) {
      return '<div class="jld-skylab-hud-sensors" data-empty="1">\u6682\u672a\u53d1\u73b0\u4f20\u611f\u5668</div>';
    }
    const buttons = sensors
      .map(sensor => {
        const type = normalizeHudSensorType(sensor.type);
        const sensorState = hudSensorState(sensor, device);
        return (
          '<button type="button" data-action="open-sensor-panel" data-hud-device-id="' +
          esc(hudDeviceKey(device, index)) +
          '" data-sensor-type="' +
          esc(type) +
          '" data-sensor-id="' +
          esc(sensor.id || '') +
          '" data-state="' +
          esc(sensorState) +
          '" title="' +
          esc((sensor.id || '') + ' · ' + hudSensorStateLabel(sensorState)) +
          '"><span></span>' +
          esc(hudSensorLabel(type)) +
          '<small>' +
          esc(sensor.id || '') +
          '</small>' +
          '</button>'
        );
      })
      .join('');
    return '<div class="jld-skylab-hud-sensors">' + buttons + '</div>';
  }

  function renderHudDeviceWindow(device, index, statusText) {
    const global = device.global || {};
    const health = device.health || {};
    const speed = hudSpeed(device);
    const verticalSpeed = hudVerticalSpeed(device);
    return (
      '<section class="jld-skylab-hud-device" style="--hud-index:' +
      index +
      '"><div class="jld-skylab-hud-head"><div><strong>' +
      esc(device.name || device.alias || '\u672a\u547d\u540d\u8bbe\u5907') +
      '</strong><span>FleetDock HUD \u00b7 ' +
      esc(statusText) +
      '</span></div><div class="jld-skylab-hud-actions"><span data-online="' +
      (device.connected ? '1' : '0') +
      '">' +
      hudBool(device.connected, '\u5df2\u8fde\u63a5', '\u672a\u8fde\u63a5') +
      '</span></div></div><div class="jld-skylab-hud-device-body">' +
      renderHudStateStrip(device) +
      renderHudAttitudeLayer(device) +
      '<div class="jld-skylab-hud-grid">' +
      renderHudMetric(
        '\u76f8\u5bf9\u9ad8\u5ea6',
        hudNumber(global.relativeAltitudeM, 1) + ' m'
      ) +
      renderHudMetric(
        '\u6c34\u5e73\u901f\u5ea6',
        speed === '--' ? '--' : speed + ' m/s'
      ) +
      renderHudMetric(
        'Vz (NED)',
        verticalSpeed === '--' ? '--' : verticalSpeed + ' m/s'
      ) +
      renderHudMetric(
        '\u7535\u91cf',
        hudBatteryPercent(device.batteryRemaining)
      ) +
      renderHudMetric(
        '\u7edd\u5bf9\u9ad8\u5ea6',
        hudNumber(global.absoluteAltitudeM, 1) + ' m'
      ) +
      renderHudMetric('\u7740\u9646\u72b6\u6001', device.landedState || '--') +
      '</div>' +
      renderHudPosition(device) +
      '<div class="jld-skylab-hud-flags">' +
      renderHudFlag('GPS', health.globalPositionOk) +
      renderHudFlag('Local', health.localPositionOk) +
      renderHudFlag('Home', health.homePositionOk) +
      renderHudFlag('Armable', health.armable) +
      '</div>' +
      renderHudSensorButtons(device, index) +
      '<div class="jld-skylab-hud-event"><span>' +
      esc(device.lastEvent?.type || '\u6682\u65e0\u4e8b\u4ef6') +
      '</span><time>' +
      esc(device.lastEvent?.receivedAt || device.updatedAt || '--') +
      '</time>' +
      '</div></div></section>'
    );
  }

  function renderEmbeddedHudListContent() {
    const devices = hudDevicesForDisplay();
    const online = state.hudSnapshot?.online === true;
    const connectedCount = devices.filter(device => device.connected).length;
    const statusText = online
      ? '\u8fd0\u884c\u65f6\u5728\u7ebf'
      : state.hudLoading
      ? '\u6b63\u5728\u8bfb\u53d6\u9065\u6d4b'
      : '\u7b49\u5f85 FleetDock \u8fd0\u884c\u65f6';
    const message = String(state.hudSnapshot?.message || '').trim();
    return (
      '<div class="jld-skylab-sim-hud-summary"><div><strong>HUD \u5217\u8868</strong><span>' +
      connectedCount +
      ' / ' +
      devices.length +
      ' \u5df2\u8fde\u63a5</span></div><span data-online="' +
      (online ? '1' : '0') +
      '">' +
      esc(statusText) +
      '</span></div>' +
      (message && !online
        ? '<div class="jld-skylab-sim-hud-message">' + esc(message) + '</div>'
        : '') +
      (devices.length
        ? devices
            .map((device, index) =>
              renderHudDeviceWindow(device, index, statusText)
            )
            .join('')
        : '<div class="jld-skylab-hud-empty"><div><strong>\u6682\u65e0 HUD \u8bbe\u5907</strong><span>\u7b49\u5f85\u4eff\u771f\u8bbe\u5907\u4fe1\u606f\u3002</span></div></div>')
    );
  }

  function updateEmbeddedHudList() {
    const container = document.querySelector('[data-skylab-embedded-hud-list]');
    if (!container) {
      return false;
    }
    const scrollTop = container.scrollTop;
    container.innerHTML = renderEmbeddedHudListContent();
    container.scrollTop = scrollTop;
    return true;
  }

  function renderHudDrawer() {
    return '';
  }

  function renderHudMetric(label, value) {
    return (
      '<div class="jld-skylab-hud-metric"><span>' +
      esc(label) +
      '</span><strong>' +
      esc(value) +
      '</strong></div>'
    );
  }

  function renderHudFlag(label, value) {
    return (
      '<span data-ok="' +
      (value === true ? '1' : value === false ? '0' : '') +
      '">' +
      esc(label) +
      '</span>'
    );
  }

  function hudSensorProperties(sensor) {
    const rows = [];
    const add = (label, value) => {
      if (value !== null && value !== undefined && value !== '') {
        rows.push([
          label,
          Array.isArray(value) ? value.join(', ') : String(value)
        ]);
      }
    };
    add('ID', sensor?.id);
    add('\u7c7b\u578b', hudSensorLabel(normalizeHudSensorType(sensor?.type)));
    add('\u6765\u6e90', sensor?.source);
    add(
      '\u8fd0\u884c\u65f6\u53d1\u73b0',
      sensor?.runtime === true ? 'Yes' : 'No'
    );
    add('\u5df2\u8ba2\u9605', sensor?.subscribed === true ? 'Yes' : 'No');
    add(
      '\u4e3b\u4f20\u611f\u5668',
      sensor?.primary === true ? 'Yes' : sensor?.primary === false ? 'No' : ''
    );
    add('\u9891\u7387', sensor?.rateHz ? sensor.rateHz + ' Hz' : '');
    add(
      '\u5b9e\u6d4b\u9891\u7387',
      sensor?.actualRateHz ? sensor.actualRateHz + ' Hz' : ''
    );
    add('\u63a5\u6536\u6837\u672c', sensor?.receivedSamples);
    add('\u6295\u9012\u6837\u672c', sensor?.deliveredSamples);
    add('\u4e22\u5f03\u6837\u672c', sensor?.droppedSamples);
    add('\u5904\u7406\u5668\u9519\u8bef', sensor?.handlerErrorCount);
    add('\u6700\u8fd1\u6837\u672c', sensor?.sampleReceivedAt);
    add('\u6d41\u9519\u8bef', sensor?.streamError);
    const config = sensor?.config || {};
    const configLabels = {
      width: '\u5bbd\u5ea6',
      height: '\u9ad8\u5ea6',
      fovDegrees: 'FOV',
      captureInterval: '\u91c7\u96c6\u95f4\u9694',
      streamingEnabled: '\u6d41\u5f0f\u8f93\u51fa',
      range: '\u91cf\u7a0b',
      maxRange: '\u6700\u5927\u91cf\u7a0b',
      numberOfChannels: '\u901a\u9053\u6570',
      pointsPerSecond: '\u70b9\u6570/\u79d2'
    };
    for (const [key, value] of Object.entries(config)) {
      if (
        ['string', 'number', 'boolean'].includes(typeof value) ||
        Array.isArray(value)
      ) {
        add(configLabels[key] || key, value);
      }
      if (rows.length >= 12) {
        break;
      }
    }
    return rows;
  }

  function hudSensorSampleRows(sample) {
    const rows = [];
    const labels = {
      sequence: '\u5e8f\u53f7',
      frame: '\u5750\u6807\u7cfb',
      captured_at_s: '\u91c7\u96c6\u65f6\u95f4',
      received_at_s: '\u63a5\u6536\u65f6\u95f4',
      clock_domain: '\u65f6\u949f\u57df',
      angular_velocity_rad_s: '\u89d2\u901f\u5ea6 rad/s',
      linear_acceleration_m_s2: '\u7ebf\u52a0\u901f\u5ea6 m/s\u00b2',
      orientation_rpy_deg: '\u59ff\u6001 RPY \u00b0',
      latitude_deg: '\u7eac\u5ea6',
      longitude_deg: '\u7ecf\u5ea6',
      absolute_altitude_m: '\u7edd\u5bf9\u9ad8\u5ea6 m',
      horizontal_accuracy_m: '\u6c34\u5e73\u7cbe\u5ea6 m',
      vertical_accuracy_m: '\u5782\u76f4\u7cbe\u5ea6 m',
      velocity_m_s: '\u901f\u5ea6 m/s',
      ground_speed_m_s: '\u5730\u901f m/s',
      heading_deg: '\u822a\u5411 \u00b0',
      satellite_count: '\u536b\u661f\u6570',
      fix_type: '\u5b9a\u4f4d\u7c7b\u578b',
      magnetic_field_t: '\u78c1\u573a T',
      temperature_c: '\u6e29\u5ea6 \u00b0C',
      absolute_pressure_pa: '\u7edd\u5bf9\u6c14\u538b Pa',
      differential_pressure_pa: '\u5dee\u538b Pa',
      pressure_altitude_m: '\u6c14\u538b\u9ad8\u5ea6 m',
      indicated_airspeed_m_s: '\u6307\u793a\u7a7a\u901f m/s',
      true_airspeed_m_s: '\u771f\u7a7a\u901f m/s',
      groundspeed_m_s: '\u5730\u901f m/s',
      distance_m: '\u8ddd\u79bb m',
      min_distance_m: '\u6700\u5c0f\u8ddd\u79bb m',
      max_distance_m: '\u6700\u5927\u8ddd\u79bb m',
      quality: '\u8d28\u91cf',
      ground_distance_m: '\u79bb\u5730\u8ddd\u79bb m'
    };
    const format = value => {
      if (typeof value === 'number') {
        const absolute = Math.abs(value);
        if (absolute > 0 && absolute < 0.001) {
          return value.toExponential(3);
        }
        return Number.isInteger(value) ? String(value) : value.toFixed(4);
      }
      if (Array.isArray(value)) {
        return value.map(format).join(' / ');
      }
      return String(value);
    };
    const visit = (value, path, depth) => {
      if (value === null || value === undefined || value === '') {
        return;
      }
      if (
        typeof value !== 'object' ||
        (Array.isArray(value) && value.every(item => typeof item !== 'object'))
      ) {
        const key = path.split('.').pop() || path;
        rows.push([labels[key] || path, format(value)]);
        return;
      }
      if (depth >= 2 || rows.length >= 32) {
        rows.push([path, JSON.stringify(value)]);
        return;
      }
      for (const [key, item] of Object.entries(value)) {
        if (key === 'sensor_id' || key === 'kind' || key === 'extensions') {
          continue;
        }
        visit(item, path ? path + '.' + key : key, depth + 1);
      }
    };
    visit(sample, '', 0);
    return rows.slice(0, 32);
  }

  function selectedHudSensor() {
    const selection = state.sensorPanel;
    if (!selection) {
      return null;
    }
    const devices = hudDevicesForDisplay();
    const device = devices.find((item, index) => {
      return hudDeviceKey(item, index) === selection.deviceKey;
    });
    const sensors = device ? hudSensorInventory(device) : [];
    const sensor = sensors.find(
      item => String(item?.id || '') === selection.sensorId
    ) || { id: selection.sensorId, type: selection.type };
    return {
      selection,
      device,
      sensor,
      type: normalizeHudSensorType(sensor?.type || selection.type)
    };
  }

  function renderSensorPanelBody(context) {
    const { device, sensor, type } = context;
    const sensorState = hudSensorState(sensor, device);
    const sampleRows = hudSensorSampleRows(sensor?.sample);
    const properties = hudSensorProperties(sensor);
    const streamMessage = sensor?.streamError
      ? String(sensor.streamError)
      : '\u5df2\u53d1\u73b0\u4f20\u611f\u5668\uff0c\u6b63\u5728\u7b49\u5f85\u7b2c\u4e00\u4e2a\u6837\u672c\u3002';
    if (type === 'camera') {
      const streams = ensureArray(state.cameraMedia.streams);
      const mediaStatus = state.simulationStatus?.mediaService;
      const viewport = streams.length
        ? '<div class="jld-skylab-camera-grid" data-count="' +
          streams.length +
          '">' +
          streams
            .map(
              stream =>
                '<article class="jld-skylab-camera-card" data-skylab-camera-card="' +
                esc(stream.id) +
                '"><video autoplay muted playsinline data-skylab-camera-video="' +
                esc(stream.id) +
                '"></video><div class="jld-skylab-camera-head"><strong>' +
                esc(stream.vehicleName) +
                '</strong><span>' +
                esc(stream.label || stream.sensorId) +
                '</span></div><div class="jld-skylab-camera-state" data-skylab-camera-state>\u6b63\u5728\u534f\u5546 WebRTC</div><div class="jld-skylab-camera-stats" data-skylab-camera-stats>-- fps · -- kbps · --</div></article>'
            )
            .join('') +
          '</div>'
        : '<div class="jld-skylab-sensor-placeholder"><strong>' +
          (state.cameraMedia.loading
            ? '\u6b63\u5728\u8bfb\u53d6\u591a\u8def\u76f8\u673a...'
            : '\u6682\u65e0\u53ef\u64ad\u653e\u7684\u76f8\u673a') +
          '</strong><span>' +
          esc(
            state.cameraMedia.error ||
              mediaStatus?.lastError ||
              '\u8bf7\u786e\u8ba4 FleetDock RTSP \u4e0e MediaMTX WebRTC \u670d\u52a1\u5df2\u5c31\u7eea\u3002'
          ) +
          '</span></div>';
      return (
        '<div class="jld-skylab-sensor-viewport jld-skylab-camera-viewport" data-live="' +
        (streams.length ? '1' : '0') +
        '">' +
        viewport +
        '</div>'
      );
    }
    const viewport = sampleRows.length
      ? '<div class="jld-skylab-sensor-live-grid">' +
        sampleRows
          .map(
            row =>
              '<div><span>' +
              esc(row[0]) +
              '</span><strong>' +
              esc(row[1]) +
              '</strong></div>'
          )
          .join('') +
        '</div>'
      : '<div class="jld-skylab-sensor-placeholder"><strong>' +
        '\u7b49\u5f85\u5b9e\u65f6\u6837\u672c' +
        '</strong><span>' +
        esc(streamMessage) +
        '</span></div>';
    return (
      '<div class="jld-skylab-sensor-viewport" data-live="' +
      (sampleRows.length ? '1' : '0') +
      '">' +
      viewport +
      '</div><aside data-skylab-sensor-aside><div class="jld-skylab-sensor-status"><span data-state="' +
      esc(sensorState) +
      '">' +
      hudSensorStateLabel(sensorState) +
      '</span><span data-skylab-sensor-frame-age>AGE ' +
      esc(hudTelemetryAge(sensor?.sampleAgeMs)) +
      '</span>' +
      '<span>' +
      esc(hudVehicleName(device || {})) +
      '</span></div><dl>' +
      properties
        .map(
          row =>
            '<div><dt>' +
            esc(row[0]) +
            '</dt><dd>' +
            esc(row[1]) +
            '</dd></div>'
        )
        .join('') +
      '</dl></aside>'
    );
  }

  function renderSensorPanel() {
    const context = selectedHudSensor();
    if (!context) {
      return '';
    }
    const { device, type } = context;
    return (
      '<section class="jld-skylab-sensor-panel" data-open="1" data-sensor-type="' +
      esc(type) +
      '"><header' +
      (type === 'camera' ? ' data-skylab-sensor-drag-handle' : '') +
      '><div><small>FleetDock SENSOR</small><strong>' +
      esc(
        (device?.name || device?.alias || '\u672a\u77e5\u8bbe\u5907') +
          ' / ' +
          hudSensorLabel(type)
      ) +
      '</strong></div><button type="button" data-action="close-sensor-panel" aria-label="\u5173\u95ed">&times;</button></header>' +
      '<div class="jld-skylab-sensor-panel-body" data-skylab-sensor-panel-body>' +
      renderSensorPanelBody(context) +
      '</div></section>'
    );
  }

  function updateSensorPanelData(forceCamera) {
    const context = selectedHudSensor();
    const body = document.querySelector('[data-skylab-sensor-panel-body]');
    if (!context || !body) {
      return;
    }
    // [UAV-IDE MODIFY] HUD polling must never replace live WebRTC video elements.
    if (context.type === 'camera' && !forceCamera) {
      return;
    }
    body.innerHTML = renderSensorPanelBody(context);
    if (context.type === 'camera') {
      connectCameraPlayers();
    }
  }

  function resetCameraMedia() {
    closeCameraPlayers();
    state.cameraMedia = {
      vehicleName: '',
      streams: [],
      error: '',
      loading: false
    };
  }

  function cameraCard(streamId) {
    return Array.from(
      document.querySelectorAll('[data-skylab-camera-card]')
    ).find(card => card.getAttribute('data-skylab-camera-card') === streamId);
  }

  function setCameraState(streamId, message, stateName) {
    const card = cameraCard(streamId);
    const target = card?.querySelector('[data-skylab-camera-state]');
    if (target) {
      target.textContent = message;
      target.setAttribute('data-state', stateName || 'loading');
    }
  }

  function waitForIceGathering(peer) {
    if (peer.iceGatheringState === 'complete') {
      return Promise.resolve();
    }
    return new Promise(resolve => {
      const timeout = window.setTimeout(done, 5000);
      function done() {
        window.clearTimeout(timeout);
        peer.removeEventListener('icegatheringstatechange', changed);
        resolve();
      }
      function changed() {
        if (peer.iceGatheringState === 'complete') done();
      }
      peer.addEventListener('icegatheringstatechange', changed);
    });
  }

  async function connectCameraPlayer(stream) {
    const video = Array.from(
      document.querySelectorAll('[data-skylab-camera-video]')
    ).find(item => item.getAttribute('data-skylab-camera-video') === stream.id);
    const current = cameraPlayers.get(stream.id);
    if (!video || (current && current.video === video)) {
      return;
    }
    if (current) closeCameraPlayer(stream.id);
    if (typeof api.createUavSimulationWebRtcSession !== 'function') {
      setCameraState(
        stream.id,
        'IDE \u672a\u66b4\u9732 WebRTC \u534f\u5546\u80fd\u529b',
        'error'
      );
      return;
    }
    const peer = new RTCPeerConnection();
    const player = {
      peer,
      video,
      stream,
      sessionId: '',
      statsTimer: 0,
      lastBytes: 0,
      lastStatsAt: 0
    };
    cameraPlayers.set(stream.id, player);
    peer.addTransceiver('video', { direction: 'recvonly' });
    peer.addEventListener('track', event => {
      if (cameraPlayers.get(stream.id) !== player) return;
      video.srcObject = event.streams[0] || new MediaStream([event.track]);
      void video.play().catch(() => undefined);
    });
    peer.addEventListener('connectionstatechange', () => {
      if (cameraPlayers.get(stream.id) !== player) return;
      const status = peer.connectionState;
      setCameraState(
        stream.id,
        status === 'connected'
          ? 'LIVE'
          : status === 'failed'
          ? 'WebRTC \u8fde\u63a5\u5931\u8d25\uff0c\u6b63\u5728\u91cd\u8fde'
          : status,
        status === 'connected'
          ? 'live'
          : status === 'failed'
          ? 'error'
          : 'loading'
      );
      if (status === 'connected' && !player.statsTimer) {
        player.statsTimer = window.setInterval(
          () => updateCameraStats(player),
          1000
        );
      }
      if (status === 'failed') {
        closeCameraPlayer(stream.id);
        window.setTimeout(() => {
          const context = selectedHudSensor();
          if (context?.type === 'camera') void connectCameraPlayer(stream);
        }, 1200);
      }
    });
    try {
      setCameraState(stream.id, '\u6b63\u5728\u534f\u5546 WebRTC', 'loading');
      await peer.setLocalDescription(await peer.createOffer());
      await waitForIceGathering(peer);
      const offerSdp = peer.localDescription?.sdp || '';
      const result = await api.createUavSimulationWebRtcSession({
        streamId: stream.id,
        offerSdp
      });
      if (cameraPlayers.get(stream.id) !== player) {
        if (result?.sessionId) {
          void api.closeUavSimulationWebRtcSession?.(result.sessionId);
        }
        return;
      }
      if (!result?.ok || !result?.answerSdp) {
        throw new Error(
          result?.message || 'MediaMTX WHEP \u672a\u8fd4\u56de SDP Answer\u3002'
        );
      }
      player.sessionId = String(result.sessionId || '');
      await peer.setRemoteDescription({
        type: 'answer',
        sdp: result.answerSdp
      });
    } catch (error) {
      setCameraState(stream.id, String(error?.message || error), 'error');
      closeCameraPlayer(stream.id);
      window.setTimeout(() => {
        const context = selectedHudSensor();
        if (
          context?.type === 'camera' &&
          state.cameraMedia.streams.some(item => item.id === stream.id)
        ) {
          void connectCameraPlayer(stream);
        }
      }, 2000);
    }
  }

  async function updateCameraStats(player) {
    if (player.peer.connectionState !== 'connected') return;
    const reports = await player.peer.getStats();
    reports.forEach(report => {
      if (
        report.type !== 'inbound-rtp' ||
        (report.kind || report.mediaType) !== 'video'
      )
        return;
      const now = Number(report.timestamp || performance.now());
      const bytes = Number(report.bytesReceived || 0);
      const elapsed = player.lastStatsAt
        ? Math.max(1, now - player.lastStatsAt)
        : 0;
      const bitrate = elapsed
        ? Math.round(((bytes - player.lastBytes) * 8) / elapsed)
        : 0;
      player.lastBytes = bytes;
      player.lastStatsAt = now;
      const width = Number(report.frameWidth || player.video.videoWidth || 0);
      const height = Number(
        report.frameHeight || player.video.videoHeight || 0
      );
      const fps = Number(report.framesPerSecond || 0).toFixed(1);
      const lost = Number(report.packetsLost || 0);
      const target = cameraCard(player.stream.id)?.querySelector(
        '[data-skylab-camera-stats]'
      );
      if (target) {
        target.textContent =
          fps +
          ' fps · ' +
          bitrate +
          ' kbps · ' +
          (width && height ? width + '\u00d7' + height : '--') +
          ' · lost ' +
          lost;
      }
    });
  }

  function closeCameraPlayer(streamId) {
    const player = cameraPlayers.get(streamId);
    if (!player) return;
    cameraPlayers.delete(streamId);
    window.clearInterval(player.statsTimer);
    player.video.srcObject = null;
    player.peer.close();
    if (player.sessionId) {
      void api.closeUavSimulationWebRtcSession?.(player.sessionId);
    }
  }

  function closeCameraPlayers() {
    Array.from(cameraPlayers.keys()).forEach(closeCameraPlayer);
  }

  function connectCameraPlayers() {
    ensureArray(state.cameraMedia.streams).forEach(stream => {
      void connectCameraPlayer(stream);
    });
  }

  async function ensureCameraStreams() {
    const context = selectedHudSensor();
    if (
      !state.open ||
      state.activeTab !== 'simulation' ||
      context?.type !== 'camera' ||
      state.cameraMedia.loading
    ) {
      return;
    }
    const vehicleName = hudVehicleName(context.device || {});
    if (!vehicleName) {
      state.cameraMedia.error =
        '\u65e0\u6cd5\u786e\u5b9a\u5f53\u524d\u65e0\u4eba\u673a\u7684 vehicleName\u3002';
      updateSensorPanelData(true);
      return;
    }
    if (
      state.cameraMedia.vehicleName &&
      state.cameraMedia.vehicleName !== vehicleName
    ) {
      resetCameraMedia();
    }
    state.cameraMedia.vehicleName = vehicleName;
    if (state.cameraMedia.streams.length) {
      connectCameraPlayers();
      return;
    }
    if (typeof api.getUavSimulationCameraStreams !== 'function') {
      state.cameraMedia.error =
        '\u5f53\u524d IDE \u7248\u672c\u672a\u63a5\u5165\u591a\u8def WebRTC \u76f8\u673a\u3002\u8bf7\u5b8c\u5168\u91cd\u542f FleetDock IDEA\u3002';
      updateSensorPanelData(true);
      return;
    }
    state.cameraMedia.loading = true;
    updateSensorPanelData(true);
    try {
      const streams = ensureArray(
        await api.getUavSimulationCameraStreams(vehicleName)
      ).filter(
        stream =>
          String(stream?.vehicleName || '').toLowerCase() ===
          vehicleName.toLowerCase()
      );
      const current = selectedHudSensor();
      if (
        current?.type !== 'camera' ||
        hudVehicleName(current.device || {}).toLowerCase() !==
          vehicleName.toLowerCase()
      ) {
        return;
      }
      state.cameraMedia.streams = streams;
      state.cameraMedia.error = state.cameraMedia.streams.length
        ? ''
        : '\u5f53\u524d\u4eff\u771f\u6ca1\u6709\u53ef\u7528\u7684 FleetDock RGB \u76f8\u673a\u6d41\u3002';
    } catch (error) {
      if (
        state.cameraMedia.vehicleName.toLowerCase() ===
        vehicleName.toLowerCase()
      ) {
        state.cameraMedia.streams = [];
        state.cameraMedia.error = String(error?.message || error);
      }
    } finally {
      if (
        state.cameraMedia.vehicleName.toLowerCase() ===
        vehicleName.toLowerCase()
      ) {
        state.cameraMedia.loading = false;
        updateSensorPanelData(true);
      }
    }
  }

  function scheduleCameraStreams() {
    const context = selectedHudSensor();
    if (
      !state.open ||
      state.activeTab !== 'simulation' ||
      context?.type !== 'camera'
    ) {
      closeCameraPlayers();
      return;
    }
    // [UAV-IDE MODIFY] Fetch only stream identities over IPC, then negotiate all WebRTC tracks concurrently.
    void ensureCameraStreams();
  }

  async function refreshHudSnapshot() {
    if (!api.getFleetDockHudSnapshot || !state.hudOpen || state.hudLoading) {
      return;
    }
    state.hudLoading = true;
    try {
      state.hudSnapshot = await api.getFleetDockHudSnapshot();
    } catch (error) {
      state.hudSnapshot = {
        ok: false,
        online: false,
        devices: [],
        message: String(error?.message || error)
      };
    } finally {
      state.hudLoading = false;
      if (state.open && state.activeTab === 'simulation') {
        if (!updateEmbeddedHudList()) {
          render();
        }
        updateSensorPanelData();
      }
    }
  }

  function scheduleHudPolling() {
    window.clearInterval(window.__jldSkyLabHudTimer);
    if (
      !state.hudOpen ||
      !state.open ||
      state.activeTab !== 'simulation' ||
      !isSimulationRuntimeView()
    ) {
      return;
    }
    if (!document.hidden) {
      void refreshHudSnapshot();
    }
    window.__jldSkyLabHudTimer = window.setInterval(() => {
      if (!document.hidden) {
        void refreshHudSnapshot();
      }
    }, 500);
  }

  function simulationCommandEnabled(action) {
    const channel = state.simulationStatus?.commandChannel;
    return (
      currentSimulationLifecycleState() === 'running' &&
      typeof api.executeUavSimulationCommand === 'function' &&
      channel?.available === true &&
      ensureArray(channel.capabilities).includes(action)
    );
  }

  function simulationCommandDisabledReason(action) {
    if (currentSimulationLifecycleState() !== 'running') {
      return '\u4eff\u771f\u5c1a\u672a\u8fdb\u5165\u8fd0\u884c\u72b6\u6001';
    }
    if (typeof api.executeUavSimulationCommand !== 'function') {
      return '\u5f53\u524d IDE \u672a\u63a5\u5165\u4eff\u771f\u547d\u4ee4\u901a\u9053';
    }
    const channel = state.simulationStatus?.commandChannel;
    if (!channel?.available) {
      return (
        channel?.message ||
        '\u4eff\u771f\u547d\u4ee4\u901a\u9053\u4e0d\u53ef\u7528'
      );
    }
    if (!ensureArray(channel.capabilities).includes(action)) {
      return '\u5f53\u524d Project AirSim Client \u4e0d\u652f\u6301\u8be5\u64cd\u4f5c';
    }
    return '';
  }

  function renderSimulationCommandButton(action) {
    const command = SIMULATION_COMMANDS[action];
    const enabled = simulationCommandEnabled(action);
    const label =
      action === 'simulation.pause.toggle'
        ? state.simulationDiagnostics?.paused === true
          ? '\u7ee7\u7eed\u4eff\u771f'
          : '\u6682\u505c\u4eff\u771f'
        : command.label;
    const title = enabled
      ? command.title
      : simulationCommandDisabledReason(action);
    return (
      '<button class="jld-skylab-btn jld-skylab-sim-command-btn" data-action="' +
      (action === 'weather.set'
        ? 'toggle-simulation-weather'
        : 'simulation-command') +
      '" data-simulation-command="' +
      esc(action) +
      '" title="' +
      esc(title) +
      '"' +
      (action === 'weather.set'
        ? ' aria-expanded="' +
          (state.simulationWeatherOpen ? 'true' : 'false') +
          '"'
        : '') +
      (enabled ? '' : ' disabled aria-disabled="true"') +
      '>' +
      esc(label) +
      '</button>'
    );
  }

  function renderSimulationWeatherSettings() {
    if (!state.simulationWeatherOpen) {
      return '';
    }
    const presets = [
      ['clear', '\u6674\u5929'],
      ['rain', '\u96e8\u5929'],
      ['snow', '\u96ea\u5929'],
      ['fog', '\u96fe\u5929'],
      ['dust', '\u626c\u5c18']
    ];
    const enabled = simulationCommandEnabled('weather.set');
    return (
      '<div class="jld-skylab-sim-weather" aria-label="\u5929\u6c14\u8bbe\u7f6e">' +
      presets
        .map(
          ([preset, label]) =>
            '<button class="jld-skylab-btn" data-action="set-simulation-weather" data-weather-preset="' +
            preset +
            '" data-selected="' +
            (state.simulationWeatherPreset === preset ? '1' : '0') +
            '"' +
            (enabled ? '' : ' disabled aria-disabled="true"') +
            '>' +
            label +
            '</button>'
        )
        .join('') +
      '</div>'
    );
  }

  function simulationCommandMessage(result) {
    if (!result) {
      return '';
    }
    if (!result.ok) {
      return (
        '\u64cd\u4f5c\u5931\u8d25\uff1a' +
        (result.message || result.code || '\u672a\u77e5\u9519\u8bef')
      );
    }
    const messages = {
      'view.next': '\u89c6\u89d2\u5df2\u5207\u6362',
      'trace.toggle': '\u822a\u8ff9\u663e\u793a\u72b6\u6001\u5df2\u5207\u6362',
      'markers.clear': '\u8c03\u8bd5\u6807\u8bb0\u5df2\u6e05\u9664',
      'simulation.pause.toggle':
        result.data?.paused === true
          ? '\u4eff\u771f\u5df2\u6682\u505c'
          : '\u4eff\u771f\u5df2\u7ee7\u7eed',
      'weather.set':
        '\u5929\u6c14\u5df2\u5207\u6362\u4e3a ' +
        ({
          clear: '\u6674\u5929',
          rain: '\u96e8\u5929',
          snow: '\u96ea\u5929',
          fog: '\u96fe\u5929',
          dust: '\u626c\u5c18'
        }[result.data?.weatherPreset] || ''),
      'simulation.snapshot': '\u4eff\u771f\u72b6\u6001\u5df2\u5237\u65b0'
    };
    return (
      messages[result.action] ||
      result.message ||
      '\u64cd\u4f5c\u5df2\u5b8c\u6210'
    );
  }

  function simulationDiagnosticNumber(value, digits) {
    const number = Number(value);
    return Number.isFinite(number) ? number.toFixed(digits) : '--';
  }

  function renderSimulationDiagnostics() {
    const diagnostics = state.simulationDiagnostics;
    if (!diagnostics) {
      return '';
    }
    const simTimeSeconds = Number(diagnostics.simTimeNanos) / 1000000000;
    const poses = ensureArray(diagnostics.poses);
    return (
      '<div class="jld-skylab-sim-diagnostics"><div class="jld-skylab-sim-diagnostic-summary"><span>\u4eff\u771f\u65f6\u95f4<strong>' +
      (Number.isFinite(simTimeSeconds)
        ? simTimeSeconds.toFixed(2) + ' s'
        : '--') +
      '</strong></span><span>\u6682\u505c\u72b6\u6001<strong>' +
      (diagnostics.paused === true
        ? '\u5df2\u6682\u505c'
        : '\u8fd0\u884c\u4e2d') +
      '</strong></span><span>\u66f4\u65b0\u65f6\u95f4<strong>' +
      esc(diagnostics.updatedAt || '--') +
      '</strong></span></div><div class="jld-skylab-sim-pose-list">' +
      (poses.length
        ? poses
            .map(item => {
              const translation = item?.pose?.translation || {};
              const rotation = item?.pose?.rotation || {};
              return (
                '<div class="jld-skylab-sim-pose-row"><strong>' +
                esc(
                  item.vehicleName ||
                    item.droneId ||
                    '\u672a\u547d\u540d\u8bbe\u5907'
                ) +
                '</strong>' +
                (item.error
                  ? '<span data-error="1">' + esc(item.error) + '</span>'
                  : '<span>X ' +
                    simulationDiagnosticNumber(translation.x, 2) +
                    '  Y ' +
                    simulationDiagnosticNumber(translation.y, 2) +
                    '  Z ' +
                    simulationDiagnosticNumber(translation.z, 2) +
                    '</span><small>Q ' +
                    simulationDiagnosticNumber(rotation.w, 3) +
                    ' / ' +
                    simulationDiagnosticNumber(rotation.x, 3) +
                    ' / ' +
                    simulationDiagnosticNumber(rotation.y, 3) +
                    ' / ' +
                    simulationDiagnosticNumber(rotation.z, 3) +
                    '</small>') +
                '</div>'
              );
            })
            .join('')
        : '<div class="jld-skylab-sim-pose-empty">\u6682\u65e0\u65e0\u4eba\u673a\u4f4d\u59ff\u6570\u636e</div>') +
      '</div></div>'
    );
  }

  function renderSimulationRuntimeControls() {
    const channel = state.simulationStatus?.commandChannel;
    const pending = Number(state.simulationCommandPending) || 0;
    const result = state.simulationCommandResult;
    const channelText = channel?.available
      ? '\u4ea4\u4e92\u901a\u9053\u5df2\u8fde\u63a5'
      : channel?.message ||
        '\u6b63\u5728\u521d\u59cb\u5316\u4ea4\u4e92\u901a\u9053';
    return (
      '<div class="jld-skylab-sim-controls"><div class="jld-skylab-sim-controls-head"><div><strong>\u573a\u666f\u4ea4\u4e92</strong><span>' +
      esc(channelText) +
      '</span></div><div class="jld-skylab-sim-controls-actions"><span data-busy="' +
      (pending > 0 ? '1' : '0') +
      '">' +
      (pending > 0 ? '\u64cd\u4f5c\u961f\u5217 ' + pending : '\u5c31\u7eea') +
      '</span></div></div><div class="jld-skylab-sim-command-grid">' +
      SIMULATION_PRIMARY_COMMANDS.map(renderSimulationCommandButton).join('') +
      '</div>' +
      renderSimulationWeatherSettings() +
      '<div class="jld-skylab-sim-command-feedback" data-state="' +
      (result ? (result.ok ? 'success' : 'failed') : '') +
      '">' +
      esc(
        simulationCommandMessage(result) ||
          '\u64cd\u4f5c\u7ed3\u679c\u5c06\u663e\u793a\u5728\u8fd9\u91cc'
      ) +
      '</div>' +
      renderSimulationDiagnostics() +
      '</div>'
    );
  }

  function updateSimulationRuntimeControls() {
    const container = document.querySelector(
      '[data-skylab-simulation-controls]'
    );
    if (!container) {
      return false;
    }
    container.innerHTML = renderSimulationRuntimeControls();
    return true;
  }

  async function executeSimulationCommand(action, params = {}) {
    if (!simulationCommandEnabled(action)) {
      state.simulationCommandResult = {
        action,
        ok: false,
        code: 'UNAVAILABLE',
        message: simulationCommandDisabledReason(action)
      };
      updateSimulationRuntimeControls();
      return;
    }
    state.simulationCommandPending += 1;
    updateSimulationRuntimeControls();
    try {
      const result = await api.executeUavSimulationCommand({
        action,
        params
      });
      state.simulationCommandResult = result;
      if (result?.ok) {
        if (action === 'simulation.snapshot') {
          state.simulationDiagnostics = {
            ...(result.data || {}),
            updatedAt: new Date().toTimeString().slice(0, 8)
          };
        } else if (action === 'simulation.pause.toggle') {
          state.simulationDiagnostics = {
            ...(state.simulationDiagnostics || {}),
            paused: result.data?.paused === true,
            updatedAt: new Date().toTimeString().slice(0, 8)
          };
        } else if (action === 'weather.set') {
          state.simulationWeatherPreset =
            result.data?.weatherPreset || state.simulationWeatherPreset;
        }
      }
      if (
        result?.code === 'CHANNEL_UNAVAILABLE' ||
        result?.code === 'CLIENT_EXITED'
      ) {
        void refreshUavSimulationStatus(false);
      }
    } catch (error) {
      state.simulationCommandResult = {
        action,
        ok: false,
        code: 'IPC_ERROR',
        message: String(error?.message || error)
      };
    } finally {
      state.simulationCommandPending = Math.max(
        0,
        state.simulationCommandPending - 1
      );
      updateSimulationRuntimeControls();
    }
  }

  function renderSimulationRuntimePanel() {
    const status = state.simulationStatus || state.simulationResult || {};
    const stopping = currentSimulationLifecycleState() === 'stopping';
    return (
      '<div class="jld-skylab-sim-runtime"><div class="jld-skylab-sim-runtime-head"><div><strong>' +
      esc(status.title || '\u4eff\u771f\u5df2\u8fd0\u884c') +
      '</strong><span>' +
      esc(
        status.message ||
          '\u573a\u666f\u3001PX4 \u548c Project AirSim \u5df2\u8fde\u63a5\u3002'
      ) +
      '</span></div><button class="jld-skylab-btn" data-variant="primary" data-action="stop-simulation"' +
      (stopping ? ' disabled aria-disabled="true"' : '') +
      '>' +
      (stopping ? '\u505c\u6b62\u4e2d...' : '\u505c\u6b62\u4eff\u771f') +
      '</button></div><div data-skylab-simulation-controls>' +
      renderSimulationRuntimeControls() +
      '</div><div class="jld-skylab-sim-hud-list" data-skylab-embedded-hud-list>' +
      renderEmbeddedHudListContent() +
      '</div></div>'
    );
  }

  function renderSimulationPanel() {
    if (isSimulationRuntimeView()) {
      return renderSimulationRuntimePanel();
    }
    return (
      // [UAV-IDE MODIFY] Keep common simulation setup inline while retaining the advanced launcher as a secondary path.
      renderSimulationEnvironmentCard() +
      renderSimulationStatusCard() +
      renderSimulationConfigCard() +
      renderSimulationLogCard() +
      '<div class="jld-skylab-sim-footer"><button class="jld-skylab-btn" data-variant="primary" data-action="start-simulation"' +
      simulationStartButtonAttrs() +
      '>' +
      simulationStartButtonLabel() +
      '</button><button class="jld-skylab-btn" data-action="stop-simulation">停止仿真</button><button class="jld-skylab-btn" data-action="open-scene-manager">高级仿真配置</button></div>'
    );
  }

  function renderGroundStationPanel() {
    const status = state.groundStationStatus || {};
    const installed = !!status.installed;
    return (
      '<div class="jld-skylab-section jld-skylab-groundstation-hero"><div class="jld-skylab-groundstation-head">' +
      railIcon('groundstation') +
      '<div><span>地面站应用</span><strong>QGroundControl</strong></div><span class="jld-skylab-groundstation-state" data-running="' +
      (status.running ? '1' : '0') +
      '">' +
      esc(status.running ? '运行中' : installed ? '已就绪' : '未安装') +
      '</span></div><p>' +
      esc(
        state.groundStationLoading
          ? '正在检测 QGroundControl...'
          : status.message || '检测随 FleetDock IDEA 安装的 QGroundControl。'
      ) +
      '</p><div class="jld-skylab-groundstation-meta"><div><span>程序路径</span><strong title="' +
      esc(status.executablePath || '') +
      '">' +
      esc(status.executablePath || '未找到') +
      '</strong></div><div><span>版本</span><strong>' +
      esc(status.version || '由外部安装提供') +
      '</strong></div></div><div class="jld-skylab-groundstation-controls"><div class="jld-skylab-groundstation-main-actions"><button class="jld-skylab-btn" data-variant="primary" data-action="launch-groundstation" aria-label="启动 QGroundControl"' +
      (!installed ||
      status.running ||
      state.groundStationLaunching ||
      state.groundStationClosing
        ? ' disabled aria-disabled="true"'
        : '') +
      '>' +
      (state.groundStationLaunching ? '启动中...' : '启动') +
      '</button><button class="jld-skylab-btn" data-action="close-groundstation" aria-label="关闭 QGroundControl"' +
      (!status.running ||
      state.groundStationLaunching ||
      state.groundStationClosing
        ? ' disabled aria-disabled="true"'
        : '') +
      '>' +
      (state.groundStationClosing ? '关闭中...' : '关闭') +
      '</button></div><div class="jld-skylab-groundstation-secondary-actions"><button class="jld-skylab-btn" data-action="select-groundstation-executable">选择程序</button><button class="jld-skylab-btn" data-action="open-groundstation-directory"' +
      (!installed ? ' disabled aria-disabled="true"' : '') +
      '>打开目录</button></div></div></div>'
    );
  }

  // [UAV-IDE MODIFY] Render read-only prerequisite checks with explicit repair suggestions.
  function renderSimulationEnvironmentCard() {
    const report = state.simulationEnvironment;
    const items = ensureArray(report?.items);
    const failedItems = items.filter(item => !item.ok);
    const rows = state.simulationEnvironmentChecking
      ? '<div class="jld-skylab-env-checking" role="status"><span></span><div><strong>正在检测仿真环境</strong><em>请等待 WSL2、PX4、Project AirSim 和端口检测完毕...</em></div></div>'
      : report?.error
      ? '<div class="jld-skylab-env-row" data-ok="0" title="' +
        esc(report.error) +
        '"><span>!</span><div><strong>检测失败</strong><em>' +
        esc(report.error) +
        '</em></div></div>'
      : items.length && !failedItems.length
      ? '<div class="jld-skylab-env-pass" title="仿真环境检测通过"><span>&#10003;</span><strong>仿真环境检测通过</strong></div>'
      : failedItems.length
      ? failedItems
          .map(item => {
            const detail = [item.message, item.detail, item.suggestion]
              .filter(Boolean)
              .join('\n');
            return (
              '<div class="jld-skylab-env-row" data-ok="' +
              (item.ok ? '1' : '0') +
              '" title="' +
              esc(detail) +
              '"><span>' +
              (item.ok ? '&#10003;' : '!') +
              '</span><div><strong>' +
              esc(item.label) +
              '</strong><em>' +
              esc(item.message) +
              '</em>' +
              (item.ok || !item.suggestion
                ? ''
                : '<small>' + esc(item.suggestion) + '</small>') +
              '</div></div>'
            );
          })
          .join('')
      : '<div class="jld-skylab-compact-empty">尚未检测仿真环境。</div>';
    return (
      '<div class="jld-skylab-section jld-skylab-sim-card">' +
      '<div class="jld-skylab-log-head"><div class="jld-skylab-section-title">仿真环境</div><button class="jld-skylab-link-btn" data-action="check-simulation-environment"' +
      (state.simulationEnvironmentChecking
        ? ' disabled aria-disabled="true"'
        : '') +
      '>' +
      (state.simulationEnvironmentChecking ? '检测中...' : '重新检测') +
      '</button></div>' +
      '<div class="jld-skylab-env-list">' +
      rows +
      '</div></div>'
    );
  }
  function renderSimulationStatusCard() {
    const status = getSimulationStatusModel();
    return (
      '<div class="jld-skylab-section jld-skylab-sim-card">' +
      '<div class="jld-skylab-section-title">仿真状态</div>' +
      '<div class="jld-skylab-sim-state-row"><div class="jld-skylab-spinner" data-active="' +
      (status.running ? '1' : '0') +
      '"></div><div class="jld-skylab-sim-state-text"><strong>' +
      esc(status.title) +
      '</strong><span>' +
      esc(status.message) +
      '</span></div><div class="jld-skylab-sim-percent">' +
      status.progress +
      '%</div></div><div class="jld-skylab-progress"><span style="width:' +
      status.progress +
      '%"></span></div><div class="jld-skylab-sim-time-row"><span>已用时间</span><strong>' +
      status.elapsed +
      '</strong><span>预计剩余</span><strong>' +
      status.remaining +
      '</strong></div></div>'
    );
  }

  function renderSimulationConfigCard() {
    const simulationDevices = state.devices;
    const scene = selectedSimulationScene();
    const sceneOptions = state.simulationScenes.length
      ? state.simulationScenes
          .map(
            item =>
              '<option value="' +
              esc(item.id) +
              '" ' +
              (item.id === state.selectedSimulationSceneId ? 'selected' : '') +
              '>' +
              esc(item.name) +
              '</option>'
          )
          .join('')
      : '<option value="">未添加场景</option>';
    const deviceOptions = simulationDevices.length
      ? simulationDevices
          .map(
            device =>
              '<label class="jld-skylab-sim-device-option"><input type="checkbox" data-action="toggle-sim-device" data-device-id="' +
              esc(device.id) +
              '" ' +
              (state.selectedSimulationDeviceIds.includes(device.id)
                ? 'checked'
                : '') +
              '><span>' +
              esc(device.name || device.id) +
              '<em>' +
              esc(device.source === 'real' ? '物理' : '仿真') +
              '</em>' +
              '</span></label>'
          )
          .join('')
      : '<span class="jld-skylab-sim-muted">当前设备列表还没有设备</span>';
    return (
      '<div class="jld-skylab-section jld-skylab-sim-card">' +
      '<div class="jld-skylab-section-title">仿真配置</div><div class="jld-skylab-sim-config">' +
      renderSimulationConfigRow('仿真类型', 'SkySim') +
      '<div class="jld-skylab-sim-config-row"><span>设备选择</span><div class="jld-skylab-sim-device-row">' +
      deviceOptions +
      '</div></div><div class="jld-skylab-sim-config-row"><span>场景选择</span><div class="jld-skylab-scene-select-row"><select data-action="select-simulation-scene">' +
      sceneOptions +
      '</select><button class="jld-skylab-icon-btn" data-action="open-scene-manager" title="场景管理">&#9881;</button></div></div>' +
      (scene
        ? '<div class="jld-skylab-sim-config-row"><span></span><div class="jld-skylab-sim-muted">' +
          esc(scene.executablePath) +
          '</div></div>'
        : '') +
      '</div></div>'
    );
  }

  function renderSimulationConfigRow(label, value) {
    return (
      '<div class="jld-skylab-sim-config-row"><span>' +
      esc(label) +
      '</span><strong>' +
      esc(value) +
      '</strong></div>'
    );
  }

  // [UAV-IDE MODIFY] Present scene maintenance as a list-first dialog with toolbar actions and inline name editing.
  function renderSimulationPathField(label, field, placeholder) {
    return (
      '<div class="jld-skylab-field jld-skylab-path-field"><label for="jld-skylab-' +
      esc(field) +
      '">' +
      esc(label) +
      '</label><div class="jld-skylab-path-picker"><input id="jld-skylab-' +
      esc(field) +
      '" data-scope="simulation-config" data-field="' +
      esc(field) +
      '" value="' +
      esc(state.simulationConfigDraft[field] || '') +
      '" title="' +
      esc(state.simulationConfigDraft[field] || '') +
      '" placeholder="' +
      esc(placeholder) +
      '"><button type="button" data-action="browse-simulation-config-directory" data-config-field="' +
      esc(field) +
      '" title="\u9009\u62e9\u6587\u4ef6\u5939" aria-label="\u4e3a ' +
      esc(label) +
      ' \u9009\u62e9\u6587\u4ef6\u5939"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3.5 7.5h6l2-2h3l2 2h4v10.5a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 18Z"></path><path d="M3.5 9.5h17"></path></svg></button></div></div>'
    );
  }

  function renderSceneManager() {
    if (!state.sceneManagerOpen) {
      return '';
    }
    const draft = state.sceneDraft || defaultSceneDraft();
    const selectedId =
      state.sceneManagerSelectedId ||
      state.selectedSimulationSceneId ||
      state.simulationScenes[0]?.id ||
      '';
    const rows = state.simulationScenes.length
      ? state.simulationScenes
          .map(scene => {
            const editing = draft.id === scene.id;
            const selected = selectedId === scene.id;
            return (
              '<div class="jld-skylab-scene-row' +
              (selected ? ' is-selected' : '') +
              '" data-action="select-scene-manager-row" data-scene-id="' +
              esc(scene.id) +
              '"><div class="jld-skylab-scene-row-main">' +
              (editing
                ? '<input class="jld-skylab-scene-name-input" data-scope="scene" data-field="name" value="' +
                  esc(draft.name || '') +
                  '" data-action="scene-edit-input" aria-label="场景名称">'
                : '<strong>' + esc(scene.name) + '</strong>') +
              '<span title="' +
              esc(scene.rootDirectory || scene.executablePath) +
              '">' +
              esc(scene.rootDirectory || scene.executablePath) +
              '</span></div><div class="jld-skylab-scene-actions">' +
              (editing
                ? '<button class="jld-skylab-btn" data-action="cancel-scene-edit">取消</button><button class="jld-skylab-btn" data-variant="primary" data-action="save-scene-inline">' +
                  (state.savingScene ? '保存中...' : '保存') +
                  '</button>'
                : '<button class="jld-skylab-btn" data-action="edit-scene" data-scene-id="' +
                  esc(scene.id) +
                  '">编辑</button>') +
              '</div></div>'
            );
          })
          .join('')
      : '<div class="jld-skylab-compact-empty">还没有添加 Project AirSim 场景。</div>';
    return (
      '<div class="jld-skylab-modal-backdrop" data-action="close-scene-manager"><div class="jld-skylab-modal" role="dialog" aria-modal="true">' +
      '<div class="jld-skylab-modal-head"><div><strong>\u4eff\u771f\u914d\u7f6e</strong><span>\u5168\u5c40 PX4 \u8def\u5f84\u548c Project AirSim \u573a\u666f\u5305\u914d\u7f6e</span></div><button class="jld-skylab-close" data-action="close-scene-manager" aria-label="\u5173\u95ed">&times;</button></div>' +
      '<div class="jld-skylab-modal-body">' +
      renderSimulationPathField(
        'PX4 \u8def\u5f84',
        'px4Path',
        'IDE install dir\\\\lib\\\\PX4'
      ) +
      renderSimulationPathField(
        '\u4eff\u771f\u573a\u666f\u4f9d\u8d56\u5305\u5b89\u88c5\u8def\u5f84',
        'scenePackageDirectory',
        'IDE install dir\\\\lib\\\\SkyLabSim'
      ) +
      '<div class="jld-skylab-scene-toolbar jld-skylab-scene-toolbar-right"><button class="jld-skylab-btn" data-variant="primary" data-action="save-simulation-config">\u4fdd\u5b58\u914d\u7f6e</button></div>' +
      '</div></div></div>'
    );
  }
  function renderSimulationLogCard() {
    const logs = state.simulationLogs.length
      ? state.simulationLogs
      : [{ time: '--:--:--', message: '等待启动仿真环境。' }];
    return (
      '<div class="jld-skylab-section jld-skylab-sim-card">' +
      '<div class="jld-skylab-log-head"><div class="jld-skylab-section-title">启动日志</div><button class="jld-skylab-link-btn" data-action="clear-simulation-log">清空</button></div>' +
      renderSimulationStages(state.simulationResult) +
      '<div class="jld-skylab-sim-log-list">' +
      logs
        .map(
          item =>
            '<div class="jld-skylab-sim-log-line"><span>[' +
            esc(item.time) +
            ']</span><em>i</em><strong>' +
            esc(item.message) +
            '</strong></div>'
        )
        .join('') +
      '</div></div>'
    );
  }

  function renderPublishPanel() {
    return (
      '<div class="jld-skylab-section jld-skylab-section-tight">' +
      '<div class="jld-skylab-section-title">发布准备</div>' +
      '<div class="jld-skylab-check-list">' +
      renderCheck(
        '应用信息',
        !!state.context?.name,
        state.context?.name || '待配置'
      ) +
      renderCheck(
        '入口文件',
        !!state.context?.entryFileExists,
        state.context?.entryFile || 'main.py'
      ) +
      renderCheck(
        'requirements.txt',
        !!state.context?.requirementsExists,
        state.context?.requirementsExists ? '已找到' : '发布向导将继续检查'
      ) +
      renderCheck(
        '环境变量',
        getEnvCount() > 0,
        getEnvCount() ? getEnvCount() + ' 个变量' : '可选'
      ) +
      renderCheck('CodeAppCenter', false, '通过发布向导登录和选择应用') +
      '</div><div class="jld-skylab-actions">' +
      '<button class="jld-skylab-btn" data-action="toggle-app-edit">编辑基础信息</button>' +
      '<button class="jld-skylab-btn" data-action="advanced-app-settings">高级应用设置</button>' +
      '<button class="jld-skylab-btn" data-variant="primary" data-action="open-publish">打开发布向导</button>' +
      '</div></div>' +
      '<div class="jld-skylab-guide"><div class="jld-skylab-section-title">发布入口说明</div><p>控制中心负责发布前检查，完整登录、应用选择、依赖扫描和上传仍由发布向导完成。</p></div>'
    );
  }

  function renderCheck(label, ok, detail) {
    return (
      '<div class="jld-skylab-check" data-ok="' +
      (ok ? '1' : '0') +
      '"><span>' +
      (ok ? '&#10003;' : '!') +
      '</span><div><strong>' +
      esc(label) +
      '</strong><small>' +
      esc(detail) +
      '</small></div></div>'
    );
  }

  function renderContent() {
    if (state.activeTab === 'devices') {
      return renderDevicesPanel();
    }
    if (state.activeTab === 'simulation') {
      return renderSimulationPanel();
    }
    if (state.activeTab === 'groundstation') {
      return renderGroundStationPanel();
    }
    if (state.activeTab === 'publish') {
      return renderPublishPanel();
    }
    return renderAppPanel();
  }

  function renderSimulationStopOverlay() {
    if (!state.stoppingSimulation) {
      return '';
    }
    return (
      '<div class="jld-skylab-stop-overlay" role="status" aria-live="polite" aria-busy="true">' +
      '<div class="jld-skylab-stop-wait"><div class="jld-skylab-spinner" data-active="1"></div>' +
      '<strong>\u6b63\u5728\u7ed3\u675f\u4eff\u771f</strong>' +
      '<span>\u6b63\u5728\u5173\u95ed\u5ba2\u6237\u7aef\u3001PX4 \u548c\u573a\u666f\u8fdb\u7a0b\uff0c\u8bf7\u7a0d\u5019...</span>' +
      '</div></div>'
    );
  }

  function render() {
    const root = ensureRoot();
    // [UAV-IDE MODIFY] Preserve live video elements across command-center status renders.
    const preservedCameraPanel =
      selectedHudSensor()?.type === 'camera'
        ? root.querySelector('.jld-skylab-sensor-panel')
        : null;
    root.setAttribute('data-open', state.open ? '1' : '0');
    root.setAttribute('aria-busy', state.stoppingSimulation ? 'true' : 'false');
    const title =
      state.activeTab === 'devices'
        ? '设备'
        : state.activeTab === 'simulation'
        ? '仿真'
        : state.activeTab === 'groundstation'
        ? '地面站'
        : state.activeTab === 'publish'
        ? '发布'
        : '应用';
    root.innerHTML =
      renderHudDrawer() +
      '<div class="jld-skylab-drawer"><div class="jld-skylab-shell">' +
      '<div class="jld-skylab-header"><div><div class="jld-skylab-title">FleetDock 控制中心</div><div class="jld-skylab-subtitle">' +
      esc(title) +
      ' 路 ' +
      esc(state.context?.name || '当前工作区') +
      '</div></div><button class="jld-skylab-close" data-action="close-center" aria-label="关闭">&times;</button></div>' +
      '<div class="jld-skylab-tabs">' +
      renderTabs() +
      '</div><div class="jld-skylab-body">' +
      renderContent() +
      '</div></div></div>' +
      '<div class="jld-skylab-rail">' +
      renderRailButton('app', 'FleetDock 控制中心', railIcon('app')) +
      renderRailButton('devices', '设备', railIcon('devices')) +
      renderRailButton('simulation', '仿真', railIcon('simulation')) +
      renderRailButton('groundstation', '地面站', railIcon('groundstation')) +
      (PUBLISH_UI_ENABLED
        ? renderRailButton('publish', '发布', railIcon('publish'))
        : '') +
      '</div>' +
      renderSimulationStopOverlay() +
      '<div class="jld-skylab-toast" data-open="' +
      (state.toast ? '1' : '0') +
      '">' +
      esc(state.toast) +
      '</div>' +
      renderSceneManager() +
      renderSensorPanel();
    if (preservedCameraPanel && state.sensorPanel) {
      root
        .querySelector('.jld-skylab-sensor-panel')
        ?.replaceWith(preservedCameraPanel);
    }
    scheduleCameraStreams();
  }

  function handleSensorPanelPointerDown(event) {
    if (event.button !== 0) {
      return;
    }
    const target = event.target instanceof Element ? event.target : null;
    const handle = target?.closest('[data-skylab-sensor-drag-handle]');
    const panel = handle?.closest(
      '.jld-skylab-sensor-panel[data-sensor-type="camera"]'
    );
    if (!handle || !panel || target?.closest('button')) {
      return;
    }

    const rect = panel.getBoundingClientRect();
    const startX = event.clientX;
    const startY = event.clientY;
    panel.style.left = rect.left + 'px';
    panel.style.top = rect.top + 'px';
    panel.style.right = 'auto';
    panel.style.bottom = 'auto';
    panel.setAttribute('data-dragging', '1');
    handle.setPointerCapture?.(event.pointerId);

    const move = moveEvent => {
      const maxLeft = Math.max(8, window.innerWidth - rect.width - 8);
      const maxTop = Math.max(8, window.innerHeight - rect.height - 8);
      panel.style.left =
        Math.min(maxLeft, Math.max(8, rect.left + moveEvent.clientX - startX)) +
        'px';
      panel.style.top =
        Math.min(maxTop, Math.max(8, rect.top + moveEvent.clientY - startY)) +
        'px';
    };
    const finish = finishEvent => {
      handle.removeEventListener('pointermove', move);
      handle.removeEventListener('pointerup', finish);
      handle.removeEventListener('pointercancel', finish);
      panel.removeAttribute('data-dragging');
      if (handle.hasPointerCapture?.(finishEvent.pointerId)) {
        handle.releasePointerCapture(finishEvent.pointerId);
      }
    };
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', finish);
    handle.addEventListener('pointercancel', finish);
    event.preventDefault();
  }

  function handleInput(event) {
    const target = event.target instanceof Element ? event.target : null;
    if (
      target instanceof HTMLSelectElement &&
      target.dataset.action === 'select-simulation-scene'
    ) {
      void selectSimulationScene(target.value);
      return;
    }
    const field = target?.getAttribute('data-field');
    const scope = target?.getAttribute('data-scope');
    if (!field || !scope) {
      return;
    }
    const value =
      target instanceof HTMLInputElement && target.type === 'checkbox'
        ? target.checked
        : target instanceof HTMLInputElement && target.type === 'number'
        ? target.value
        : target instanceof HTMLInputElement ||
          target instanceof HTMLSelectElement
        ? target.value
        : '';
    if (scope === 'app') {
      state.appDraft[field] = value;
    }
    if (scope === 'simulation-config') {
      state.simulationConfigDraft[field] = value;
    }
    if (scope === 'device' && state.deviceDraft) {
      state.deviceDraft[field] =
        field === 'allowCommand'
          ? value === 'true'
          : field === 'envVarSync' || field === 'enabled'
          ? !!value
          : value;
      if (field === 'runtimeKind') {
        state.deviceDraft.source = value === 'physical' ? 'real' : 'simulation';
        state.deviceDraft.accessType =
          value === 'physical' ? 'physical' : 'virtual';
        if (value === 'physical') {
          state.deviceDraft.px4StartMode = 'manual-connect';
        }
      }
      if (field === 'source') {
        state.deviceDraft.accessType =
          value === 'real' ? 'physical' : 'virtual';
        state.deviceDraft.runtimeKind =
          value === 'real' ? 'physical' : 'simulation';
        if (value === 'real') {
          state.deviceDraft.px4StartMode = 'manual-connect';
        }
      }
      if (field === 'airframeType') {
        const airframe = getUavAirframeProfile(value);
        state.deviceDraft.model = value;
        state.deviceDraft.px4AutostartId = airframe.px4AutostartId;
        state.deviceDraft.projectAirsimAirframe =
          airframe.projectAirsimAirframe;
        state.deviceDraft.robotTemplate = airframe.robotTemplate;
      }
      if (field === 'px4StartMode' && value === 'ide-managed') {
        state.deviceDraft.connectionUri = state.deviceDraft.connectionUri || '';
      }
      // [UAV-IDE MODIFY] Re-render structural device fields when source, launch mode, or env sync changes.
      if (
        field === 'source' ||
        field === 'runtimeKind' ||
        field === 'deviceCategory' ||
        field === 'airframeType' ||
        field === 'px4StartMode' ||
        field === 'envVarSync'
      ) {
        render();
      }
    }
    if (scope === 'scene' && state.sceneDraft) {
      state.sceneDraft[field] = value;
    }
  }

  function handleClick(event) {
    const target = event.target instanceof Element ? event.target : null;
    const actionEl = target?.closest('[data-action]');
    if (!actionEl) {
      return;
    }
    const action = actionEl.getAttribute('data-action');
    const tab = normalizeTab(actionEl.getAttribute('data-tab') || '');
    const deviceId = actionEl.getAttribute('data-device-id') || '';
    const bindingId = actionEl.getAttribute('data-binding-id') || '';
    const sceneId = actionEl.getAttribute('data-scene-id') || '';
    const hudDeviceId = actionEl.getAttribute('data-hud-device-id') || '';
    const sensorType = actionEl.getAttribute('data-sensor-type') || '';
    const sensorId = actionEl.getAttribute('data-sensor-id') || '';
    const simulationCommand =
      actionEl.getAttribute('data-simulation-command') || '';
    const weatherPreset = actionEl.getAttribute('data-weather-preset') || '';

    if (action === 'open-tab') {
      if (state.open && state.activeTab === tab) {
        closeCenter();
      } else {
        openCenter(tab);
      }
      return;
    }
    if (action === 'switch-tab') {
      openCenter(tab);
      return;
    }
    if (action === 'close-center') {
      closeCenter();
      return;
    }
    if (action === 'close-hud-device') {
      if (hudDeviceId && !state.hudClosedDeviceIds.includes(hudDeviceId)) {
        state.hudClosedDeviceIds.push(hudDeviceId);
      }
      if (
        !hudDevicesForDisplay().some((device, index) => {
          return !state.hudClosedDeviceIds.includes(
            hudDeviceKey(device, index)
          );
        })
      ) {
        state.hudOpen = false;
      }
      render();
      scheduleHudPolling();
      return;
    }
    if (action === 'open-sensor-panel') {
      state.sensorPanel = {
        deviceKey: hudDeviceId,
        type: normalizeHudSensorType(sensorType),
        sensorId
      };
      resetCameraMedia();
      render();
      scheduleCameraStreams();
      return;
    }
    if (action === 'close-sensor-panel') {
      state.sensorPanel = null;
      resetCameraMedia();
      render();
      return;
    }
    if (action === 'toggle-app-edit') {
      state.activeTab = 'app';
      state.appEditing = !state.appEditing;
      state.appDraft = buildAppDraft();
      render();
      return;
    }
    if (action === 'save-app-quick') {
      void saveAppQuickEdit();
      return;
    }
    if (action === 'advanced-app-settings') {
      api.showApplicationSettings?.();
      return;
    }
    if (action === 'open-publish') {
      api.showCodeAppCenterPublish?.();
      return;
    }
    if (action === 'launch-groundstation') {
      void launchGroundStation();
      return;
    }
    if (action === 'close-groundstation') {
      void closeGroundStation();
      return;
    }
    if (action === 'select-groundstation-executable') {
      void selectGroundStationExecutable();
      return;
    }
    if (action === 'open-groundstation-directory') {
      void openGroundStationInstallDirectory();
      return;
    }
    if (action === 'new-device') {
      api.openDeviceManager?.();
      return;
    }
    if (action === 'new-sim-device') {
      api.openDeviceManager?.();
      return;
    }
    if (action === 'new-real-device') {
      api.openDeviceManager?.();
      return;
    }
    if (action === 'edit-device') {
      api.openDeviceManager?.();
      return;
    }
    if (action === 'delete-device') {
      api.openDeviceManager?.();
      return;
    }
    if (action === 'cancel-device-edit') {
      state.deviceEditorOpen = false;
      state.deviceDraft = null;
      render();
      return;
    }
    if (action === 'save-device-inline') {
      void saveDeviceInline();
      return;
    }
    if (action === 'open-device-manager') {
      api.openDeviceManager?.(deviceId || undefined);
      return;
    }
    if (action === 'open-real-debugger') {
      const device = state.devices.find(
        item => String(item?.id || '') === deviceId
      );
      if (!device) {
        toast(
          '\u672a\u627e\u5230\u8981\u8c03\u8bd5\u7684\u7269\u7406\u8bbe\u5907'
        );
        return;
      }
      if (!window.jlabDesktopRealDeviceDebugger?.open) {
        toast(
          '\u771f\u673a\u8c03\u8bd5\u5de5\u4f5c\u53f0\u5c1a\u672a\u52a0\u8f7d'
        );
        return;
      }
      void window.jlabDesktopRealDeviceDebugger.open(device);
      return;
    }
    if (action === 'save-simulation-config') {
      void saveSimulationConfigInline();
      return;
    }
    if (action === 'browse-simulation-config-directory') {
      void browseSimulationConfigDirectory(
        actionEl.getAttribute('data-config-field') || ''
      );
      return;
    }
    if (action === 'open-scene-manager') {
      openSceneManager();
      return;
    }
    if (action === 'close-scene-manager') {
      if (
        actionEl.classList.contains('jld-skylab-modal-backdrop') &&
        target !== actionEl
      ) {
        return;
      }
      state.sceneManagerOpen = false;
      state.sceneManagerSelectedId = '';
      state.sceneDraft = null;
      render();
      return;
    }
    if (action === 'save-scene-inline') {
      void saveSceneInline();
      return;
    }
    if (action === 'delete-scene') {
      void deleteSceneInline(sceneId);
      return;
    }
    if (action === 'select-scene-manager-row') {
      state.sceneManagerSelectedId = sceneId;
      render();
      return;
    }
    if (action === 'scene-edit-input') {
      return;
    }
    if (action === 'edit-scene') {
      state.sceneManagerSelectedId = sceneId;
      state.sceneDraft = defaultSceneDraft(
        state.simulationScenes.find(scene => scene.id === sceneId)
      );
      render();
      return;
    }
    if (action === 'cancel-scene-edit') {
      state.sceneDraft = defaultSceneDraft();
      render();
      return;
    }
    if (action === 'reset-scene-draft') {
      state.sceneDraft = defaultSceneDraft();
      render();
      return;
    }
    if (action === 'toggle-sim-device') {
      const checked =
        actionEl instanceof HTMLInputElement ? actionEl.checked : false;
      if (checked && !state.selectedSimulationDeviceIds.includes(deviceId)) {
        state.selectedSimulationDeviceIds.push(deviceId);
      }
      if (!checked) {
        state.selectedSimulationDeviceIds = state.selectedSimulationDeviceIds.filter(
          id => id !== deviceId
        );
      }
      render();
      return;
    }
    if (action === 'refresh-devices') {
      void refreshDevices();
      return;
    }
    if (action === 'start-simulation') {
      void startSimulation();
      return;
    }
    if (action === 'stop-simulation') {
      void stopSimulation();
      return;
    }
    if (action === 'simulation-command') {
      void executeSimulationCommand(simulationCommand);
      return;
    }
    if (action === 'toggle-simulation-weather') {
      state.simulationWeatherOpen = !state.simulationWeatherOpen;
      updateSimulationRuntimeControls();
      return;
    }
    if (action === 'set-simulation-weather') {
      void executeSimulationCommand('weather.set', {
        preset: weatherPreset
      });
      return;
    }
    if (action === 'check-simulation-environment') {
      void refreshSimulationEnvironment();
      return;
    }
    if (action === 'clear-simulation-log') {
      state.simulationLogs = [];
      render();
    }
  }

  async function initialize() {
    await refreshContext();
    await refreshSimulationScenes(false);
    await refreshDevices(false);
    const contextKey =
      STORAGE_FIRST_OPEN_PREFIX +
      (state.context?.workingDirectory || 'default');
    const hasOpenedBefore = localStorage.getItem(contextKey) === '1';
    const savedOpen = localStorage.getItem(STORAGE_OPEN);
    state.open = hasOpenedBefore ? savedOpen === '1' : true;
    localStorage.setItem(contextKey, '1');
    render();
  }

  window.jlabDesktopSkyLabCommandCenter = {
    // [UAV-IDE MODIFY] Allow shell menu commands to focus the right-side FleetDock command center.
    open: function (tab) {
      openCenter(tab);
    },
    openSimulationConfig: function () {
      openCenter('simulation');
      openSceneManager();
    },
    refresh: async function () {
      await refreshContext();
      await refreshDevices(false);
    }
  };

  window.jlabDesktopUavSimManager = {
    // [UAV-IDE MODIFY] Preserve the previous in-page UAV manager contract while routing to the FleetDock command center.
    openPanel: function (tab) {
      if (tab === 'simulation-config') {
        openCenter('simulation');
        openSceneManager();
      } else {
        openCenter(tab === 'devices' ? 'devices' : tab || 'simulation');
      }
      return Promise.resolve(true);
    },
    refresh: function () {
      return refreshDevices();
    }
  };

  void initialize();
})();
