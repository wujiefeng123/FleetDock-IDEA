(function () {
  if (window.__fleetDockWelcomeExtensionInstalled) {
    return;
  }

  const startColumn = document.querySelector('.start-col');
  if (!startColumn || !window.electronAPI?.createNewApplication) {
    return;
  }
  const upstreamContainer = document.querySelector('.container');
  const recentSessions = document.getElementById('recent-sessions-col');
  if (!upstreamContainer || !recentSessions) {
    return;
  }

  // [UAV-IDE MODIFY] Recreate the reviewed FleetDock welcome shell as an
  // isolated adapter over the upstream welcome DOM and IPC contracts.
  buildWelcomeShell(upstreamContainer, recentSessions);
  // [UAV-IDE MODIFY] Mark installation only after DOM construction succeeds;
  // a Trusted Types or early-load failure must remain retryable.
  window.__fleetDockWelcomeExtensionInstalled = true;

  function element(tagName, className, text) {
    const node = document.createElement(tagName);
    if (className) {
      node.className = className;
    }
    if (text !== undefined) {
      node.textContent = text;
    }
    return node;
  }

  function buildWelcomeShell(container, recentList) {
    container.classList.add('fleetdock-upstream-welcome');

    const root = element('div', 'fleetdock-welcome-shell');
    const header = element('header', 'fleetdock-welcome-header');
    const brand = element('div', 'fleetdock-welcome-brand');
    const brandLogo = element('div', 'fleetdock-welcome-brand-logo');
    const sourceLogo = document.querySelector('.new-session-action-row svg');
    if (sourceLogo) {
      brandLogo.appendChild(sourceLogo.cloneNode(true));
    } else {
      brandLogo.textContent = 'F';
    }
    const brandText = element('div', 'fleetdock-welcome-brand-text');
    brandText.append(
      element('div', 'fleetdock-welcome-brand-name', 'FleetDock IDEA'),
      element('div', 'fleetdock-welcome-brand-subtitle', '无人机应用开发环境')
    );
    brand.append(brandLogo, brandText);
    header.appendChild(brand);

    const content = element('main', 'fleetdock-welcome-content');
    const toolbar = element('section', 'fleetdock-welcome-toolbar');
    const searchBox = element('label', 'fleetdock-welcome-search');
    const searchIcon = element('span', 'fleetdock-welcome-search-icon', '⌕');
    searchIcon.setAttribute('aria-hidden', 'true');
    const searchInput = element('input');
    searchInput.type = 'search';
    searchInput.placeholder = '搜索应用';
    searchInput.setAttribute('aria-label', '搜索应用');
    searchBox.append(searchIcon, searchInput);
    const actions = element('div', 'fleetdock-welcome-toolbar-actions');
    const createButton = element(
      'button',
      'fleetdock-welcome-button fleetdock-welcome-button-primary',
      '新建应用'
    );
    createButton.type = 'button';
    createButton.addEventListener('click', openDialog);
    const openButton = element('button', 'fleetdock-welcome-button', '打开');
    openButton.type = 'button';
    openButton.addEventListener('click', () => {
      window.electronAPI.newSession('open');
    });
    actions.append(createButton, openButton);
    toolbar.append(searchBox, actions);

    const sectionHeading = element(
      'section',
      'fleetdock-welcome-section-heading'
    );
    sectionHeading.append(
      element('h2', 'fleetdock-welcome-section-title', '最近应用')
    );
    const count = element('span', 'fleetdock-welcome-count');
    sectionHeading.appendChild(count);

    const list = element('section', 'fleetdock-welcome-list');
    recentList.classList.add('fleetdock-welcome-recent-list');
    list.appendChild(recentList);
    const expander = document.getElementById('recent-expander-col');
    if (expander) {
      expander.classList.add('fleetdock-welcome-expander');
      list.appendChild(expander);
    }

    content.append(toolbar, sectionHeading, list);
    root.append(header, content);
    container.insertAdjacentElement('beforebegin', root);

    let decorating = false;
    const decorateRecentSessions = () => {
      if (decorating) {
        return;
      }
      decorating = true;
      const rows = Array.from(
        recentList.querySelectorAll('.recent-session-row')
      );
      for (const row of rows) {
        if (row.querySelector('.fleetdock-welcome-avatar')) {
          continue;
        }
        const link = row.querySelector('.recent-session-link');
        const detail = row.querySelector('.recent-session-detail');
        const label = String(link?.textContent || 'AP').trim();
        const avatar = element(
          'span',
          'fleetdock-welcome-avatar',
          makeInitials(label)
        );
        avatar.style.setProperty(
          '--fleetdock-avatar-color',
          avatarColor(label)
        );
        const main = element('span', 'fleetdock-welcome-recent-main');
        if (link) {
          main.appendChild(link);
        }
        if (detail) {
          main.appendChild(detail);
        }
        row.insertAdjacentElement('afterbegin', main);
        row.insertAdjacentElement('afterbegin', avatar);
        installRecentSessionInteraction(row);
      }

      const emptyMessage = recentList.querySelector('.no-recent-message');
      const emptyMessageText =
        '暂无最近应用，点击“新建应用”开始创建 FleetDock 工作空间。';
      // [UAV-IDE MODIFY] Avoid retriggering the recent-list observer when the
      // desired copy is already present; assigning textContent unconditionally
      // creates a self-sustaining mutation loop and freezes the welcome view.
      if (emptyMessage && emptyMessage.textContent !== emptyMessageText) {
        emptyMessage.textContent = emptyMessageText;
      }
      count.textContent = applySearch(rows, searchInput.value) + ' 个应用';
      decorating = false;
    };

    searchInput.addEventListener('input', () => {
      const rows = Array.from(
        recentList.querySelectorAll('.recent-session-row')
      );
      count.textContent = applySearch(rows, searchInput.value) + ' 个应用';
    });
    const observer = new MutationObserver(decorateRecentSessions);
    observer.observe(recentList, { childList: true, subtree: true });
    window.__fleetDockWelcomeExtensionObserver = observer;
    decorateRecentSessions();
    searchInput.focus({ preventScroll: true });
    return root;
  }

  function installRecentSessionInteraction(row) {
    // [UAV-IDE MODIFY] Make the whole visual application card match its
    // pointer affordance while preserving the dedicated remove action.
    row.setAttribute('role', 'button');
    row.setAttribute('tabindex', '0');
    const open = event => {
      if (
        row.dataset.fleetdockOpening === 'true' ||
        event.target.closest('.recent-session-delete')
      ) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      row.dataset.fleetdockOpening = 'true';
      row.setAttribute('aria-busy', 'true');
      const sessionIndex = Number.parseInt(row.dataset.sessionIndex, 10);
      if (Number.isInteger(sessionIndex)) {
        window.electronAPI.openRecentSession(sessionIndex);
      }
    };
    row.addEventListener('click', open, true);
    row.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') {
        open(event);
      }
    });
  }

  function makeInitials(text) {
    const chars = Array.from(String(text || '').trim()).filter(char =>
      /\S/.test(char)
    );
    if (!chars.length) {
      return 'AP';
    }
    return chars.slice(0, 2).join('').toUpperCase();
  }

  function avatarColor(text) {
    const palette = [
      '#2563eb',
      '#0891b2',
      '#7c3aed',
      '#0f766e',
      '#c2410c',
      '#be123c'
    ];
    let hash = 0;
    for (const char of String(text || '')) {
      hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
    }
    return palette[hash % palette.length];
  }

  function applySearch(rows, value) {
    const query = String(value || '')
      .trim()
      .toLocaleLowerCase();
    let visibleCount = 0;
    for (const row of rows) {
      row.hidden =
        !!query &&
        !String(row.textContent || '')
          .toLocaleLowerCase()
          .includes(query);
      if (!row.hidden) {
        visibleCount += 1;
      }
    }
    return visibleCount;
  }

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
