(function () {
  if (window.__fleetDockTitleBarExtensionInstalled) {
    return;
  }

  const title = document.getElementById('app-title');
  if (!title) {
    return;
  }

  // [UAV-IDE MODIFY] Hide the upstream environment shortcut in FleetDock's
  // simplified chrome without disabling environment management itself.
  const serverButton = document.getElementById('server-button');
  if (serverButton) {
    serverButton.hidden = true;
    serverButton.setAttribute('aria-hidden', 'true');
    serverButton.style.setProperty('display', 'none', 'important');
  }

  // [UAV-IDE MODIFY] Preserve useful document context while replacing upstream
  // product branding in the persistent desktop chrome.
  let normalizing = false;
  const normalizeTitle = () => {
    if (normalizing) {
      return;
    }
    const current = String(title.textContent || '').trim();
    const normalized =
      !current || current === 'Welcome'
        ? 'FleetDock IDEA'
        : current.replace(/JupyterLab/g, 'FleetDock IDEA');
    if (normalized !== current) {
      normalizing = true;
      title.textContent = normalized;
      normalizing = false;
    }
  };

  document.body.classList.add('fleetdock-titlebar');
  const observer = new MutationObserver(normalizeTitle);
  observer.observe(title, {
    childList: true,
    characterData: true,
    subtree: true
  });
  window.__fleetDockTitleBarExtensionObserver = observer;
  window.__fleetDockTitleBarExtensionInstalled = true;
  normalizeTitle();
})();
