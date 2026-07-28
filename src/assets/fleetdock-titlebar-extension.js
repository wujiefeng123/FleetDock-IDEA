(function () {
  if (window.__fleetDockTitleBarExtensionInstalled) {
    return;
  }

  const title = document.getElementById('app-title');
  if (!title) {
    return;
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
