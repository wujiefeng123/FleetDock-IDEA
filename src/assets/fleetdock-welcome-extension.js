(function () {
  if (window.__fleetDockWelcomeExtensionInstalled) {
    return;
  }
  window.__fleetDockWelcomeExtensionInstalled = true;

  const startColumn = document.querySelector('.start-col');
  if (!startColumn || !window.electronAPI?.createNewApplication) {
    return;
  }

  const action = document.createElement('div');
  action.className = 'row action-row fleetdock-welcome-action';
  action.innerHTML =
    '<a href="#"><span class="fleetdock-welcome-badge">F</span>' +
    '<span>创建 FleetDock 应用</span></a>';
  startColumn
    .querySelector('.row-title')
    ?.insertAdjacentElement('afterend', action);
  action.querySelector('a').addEventListener('click', event => {
    event.preventDefault();
    openDialog();
  });

  function openDialog() {
    if (document.querySelector('.fleetdock-welcome-modal')) {
      return;
    }
    const modal = document.createElement('div');
    modal.className = 'fleetdock-welcome-modal';
    modal.innerHTML = `
      <div class="fleetdock-welcome-dialog" role="dialog" aria-modal="true">
        <h2>创建 FleetDock 应用</h2>
        <div class="fleetdock-welcome-grid">
          <label class="fleetdock-welcome-field">
            <span>应用标识</span>
            <input data-field="appCode" placeholder="inspection-app">
          </label>
          <label class="fleetdock-welcome-field">
            <span>应用名称</span>
            <input data-field="name" placeholder="巡检应用">
          </label>
          <label class="fleetdock-welcome-field fleetdock-welcome-field-wide">
            <span>保存目录</span>
            <span class="fleetdock-welcome-path-row">
              <input data-field="workingDirectory" readonly>
              <button type="button" data-action="browse">选择…</button>
            </span>
          </label>
          <label class="fleetdock-welcome-field">
            <span>版本</span>
            <input data-field="version" value="0.1.0">
          </label>
          <label class="fleetdock-welcome-field">
            <span>入口文件</span>
            <input data-field="entryFile" value="main.py">
          </label>
          <label class="fleetdock-welcome-field fleetdock-welcome-field-wide">
            <span>说明</span>
            <textarea data-field="description" rows="3"></textarea>
          </label>
        </div>
        <div class="fleetdock-welcome-status" data-role="status"></div>
        <div class="fleetdock-welcome-actions">
          <button type="button" data-action="cancel">取消</button>
          <button type="button" data-action="create">创建并打开</button>
        </div>
      </div>`;
    document.body.appendChild(modal);

    const field = name => modal.querySelector(`[data-field="${name}"]`);
    const status = modal.querySelector('[data-role="status"]');
    modal.querySelector('[data-action="cancel"]').onclick = () =>
      modal.remove();
    modal.querySelector('[data-action="browse"]').onclick = async () => {
      const selected = await window.electronAPI.selectDirectoryPath(
        field('workingDirectory').value
      );
      if (selected) {
        field('workingDirectory').value = selected;
      }
    };
    modal.querySelector('[data-action="create"]').onclick = async event => {
      const button = event.currentTarget;
      button.disabled = true;
      status.textContent = '';
      try {
        const payload = {
          appCode: field('appCode').value.trim(),
          name: field('name').value.trim(),
          version: field('version').value.trim() || '0.1.0',
          description: field('description').value.trim(),
          entryFile: field('entryFile').value.trim() || 'main.py',
          maxRunSeconds: 300,
          changeLog: '',
          workingDirectory: field('workingDirectory').value.trim(),
          runtimeLabel: 'Python',
          pythonPath: '',
          envVars: [],
          aiGateway: {
            gatewayUrl: '',
            defaultModel: '',
            routeStrategy: '',
            timeoutMs: 15000,
            apiKey: '',
            ak: '',
            sk: ''
          },
          simulator: {
            engine: 'Project AirSim',
            worldFile: '',
            realTimeFactor: '1',
            autoLaunchAirSim: true
          },
          groundStation: {
            stationType: '',
            connectionUrl: '',
            autoConnect: false
          }
        };
        if (!payload.appCode || !payload.name || !payload.workingDirectory) {
          throw new Error('请填写应用标识、应用名称并选择保存目录。');
        }
        await window.electronAPI.createNewApplication(payload);
        modal.remove();
      } catch (error) {
        status.textContent = String(error?.message || error);
        button.disabled = false;
      }
    };
  }
})();
