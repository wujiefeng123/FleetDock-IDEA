// [UAV-IDE MODIFY] Keep desktop chrome aligned with the active JupyterLab theme without altering theme settings.
(function () {
  if (window.__fleetDockLabThemeObserver || !document.body) {
    return;
  }
  let previous;
  const syncTheme = () => {
    const { jpThemeLight, jpThemeName } = document.body.dataset;
    if (jpThemeLight === undefined && !jpThemeName) {
      return;
    }
    const dark =
      jpThemeLight === undefined
        ? /dark/i.test(jpThemeName)
        : jpThemeLight === 'false';
    if (dark !== previous) {
      window.electronAPI?.syncFleetDockTitleBarTheme?.(dark);
      previous = dark;
    }
  };
  const observer = new MutationObserver(syncTheme);
  observer.observe(document.body, {
    attributes: true,
    attributeFilter: ['data-jp-theme-light', 'data-jp-theme-name']
  });
  window.__fleetDockLabThemeObserver = observer;
  window.addEventListener(
    'pagehide',
    () => {
      observer.disconnect();
      delete window.__fleetDockLabThemeObserver;
    },
    { once: true }
  );
  syncTheme();
})();
