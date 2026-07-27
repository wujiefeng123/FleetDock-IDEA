(function () {
  if (window.__fleetDockWelcomeExtensionInstalled) {
    return;
  }

  const startColumn = document.querySelector('.start-col');
  if (!startColumn || !window.electronAPI?.createNewApplication) {
    return;
  }
  const rowTitle = startColumn.querySelector('.row-title');
  if (!rowTitle) {
    return;
  }

  const action = document.createElement('div');
  action.className = 'row action-row fleetdock-welcome-action';
  const actionLink = document.createElement('a');
  actionLink.href = '#';
  const badge = document.createElement('span');
  badge.className = 'fleetdock-welcome-badge';
  badge.textContent = 'F';
  const actionLabel = document.createElement('span');
  actionLabel.className = 'fleetdock-welcome-label';
  actionLabel.textContent = '创建 FleetDock 应用';
  actionLink.append(badge, actionLabel);
  action.appendChild(actionLink);
  rowTitle.insertAdjacentElement('afterend', action);
  actionLink.addEventListener('click', event => {
    event.preventDefault();
    openDialog();
  });
  // [UAV-IDE MODIFY] Upstream refreshes the start actions after asynchronous
  // environment discovery. Reattach the same extension action if that render
  // replaces its parent DOM.
  if (!window.__fleetDockWelcomeExtensionObserver) {
    const restoreAction = () => {
      if (document.documentElement.contains(action)) {
        return;
      }
      document
        .querySelector('.start-col .row-title')
        ?.insertAdjacentElement('afterend', action);
    };
    window.__fleetDockWelcomeExtensionObserver = new MutationObserver(
      restoreAction
    );
    window.__fleetDockWelcomeExtensionObserver.observe(
      document.documentElement,
      {
        childList: true,
        subtree: true
      }
    );
  }
  // [UAV-IDE MODIFY] Mark installation only after DOM construction succeeds;
  // a Trusted Types or early-load failure must remain retryable.
  window.__fleetDockWelcomeExtensionInstalled = true;

  function openDialog() {
    if (document.querySelector('.fleetdock-welcome-modal')) {
      return;
    }
    const modal = document.createElement('div');
    modal.className = 'fleetdock-welcome-modal';
    const dialog = document.createElement('div');
    dialog.className = 'fleetdock-welcome-dialog';
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');
    const heading = document.createElement('h2');
    heading.textContent = '创建 FleetDock 应用';
    const grid = document.createElement('div');
    grid.className = 'fleetdock-welcome-grid';
    const fields = {};

    const addField = (name, labelText, options = {}) => {
      const label = document.createElement('label');
      label.className =
        'fleetdock-welcome-field' +
        (options.wide ? ' fleetdock-welcome-field-wide' : '');
      const caption = document.createElement('span');
      caption.textContent = labelText;
      const input = document.createElement(
        options.multiline ? 'textarea' : 'input'
      );
      input.dataset.field = name;
      if (options.placeholder) {
        input.placeholder = options.placeholder;
      }
      if (options.value) {
        input.value = options.value;
      }
      if (options.multiline) {
        input.rows = options.rows || 3;
      }
      label.append(caption, input);
      grid.appendChild(label);
      fields[name] = input;
      return { label, input };
    };

    addField('appCode', '应用标识', { placeholder: 'inspection-app' });
    addField('name', '应用名称', { placeholder: '巡检应用' });
    const workingDirectory = addField('workingDirectory', '保存目录', {
      wide: true
    });
    workingDirectory.input.readOnly = true;
    const pathRow = document.createElement('span');
    pathRow.className = 'fleetdock-welcome-path-row';
    const browseButton = document.createElement('button');
    browseButton.type = 'button';
    browseButton.dataset.action = 'browse';
    browseButton.textContent = '选择…';
    workingDirectory.input.replaceWith(pathRow);
    pathRow.append(workingDirectory.input, browseButton);
    addField('version', '版本', { value: '0.1.0' });
    addField('entryFile', '入口文件', { value: 'main.py' });
    addField('description', '说明', {
      wide: true,
      multiline: true,
      rows: 3
    });

    const status = document.createElement('div');
    status.className = 'fleetdock-welcome-status';
    status.dataset.role = 'status';
    const actions = document.createElement('div');
    actions.className = 'fleetdock-welcome-actions';
    const cancelButton = document.createElement('button');
    cancelButton.type = 'button';
    cancelButton.dataset.action = 'cancel';
    cancelButton.textContent = '取消';
    const createButton = document.createElement('button');
    createButton.type = 'button';
    createButton.dataset.action = 'create';
    createButton.textContent = '创建并打开';
    actions.append(cancelButton, createButton);
    dialog.append(heading, grid, status, actions);
    modal.appendChild(dialog);
    document.body.appendChild(modal);

    const field = name => fields[name];
    cancelButton.onclick = () => modal.remove();
    browseButton.onclick = async () => {
      const selected = await window.electronAPI.selectDirectoryPath(
        field('workingDirectory').value
      );
      if (selected) {
        field('workingDirectory').value = selected;
      }
    };
    createButton.onclick = async event => {
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
