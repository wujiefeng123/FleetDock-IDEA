// [UAV-IDE MODIFY] Keep the FleetDock workspace mark isolated from the upstream JupyterLab shell.
(function () {
  const MARK_CLASS = 'jld-skylab-menu-logo';
  const MARKUP = `
    <svg viewBox="0 0 512 512" role="img" aria-label="FleetDock IDEA">
      <rect width="512" height="512" rx="112" fill="url(#fleetdock-lab-logo-background)"></rect>
      <path d="M221 132H177L82 224L186 328" stroke="#EAF8FF" stroke-width="34" stroke-linecap="round" stroke-linejoin="round"></path>
      <path d="M221 188H184L149 224L184 260" stroke="#EAF8FF" stroke-width="30" stroke-linecap="round" stroke-linejoin="round"></path>
      <path d="M284 130L384 224L290 330H235" stroke="#16C7E8" stroke-width="34" stroke-linecap="round" stroke-linejoin="round"></path>
      <path d="M211 225H294L244 278H211" stroke="#16C7E8" stroke-width="30" stroke-linecap="round" stroke-linejoin="round"></path>
      <circle cx="82" cy="224" r="22" fill="#16C7E8"></circle>
      <circle cx="82" cy="224" r="10" fill="#062E54"></circle>
      <circle cx="284" cy="130" r="22" fill="#22C55E"></circle>
      <circle cx="284" cy="130" r="10" fill="#EAF8FF"></circle>
      <circle cx="235" cy="330" r="22" fill="#EAF8FF"></circle>
      <circle cx="235" cy="330" r="10" fill="#062E54"></circle>
      <defs>
        <linearGradient id="fleetdock-lab-logo-background" x1="58" y1="44" x2="454" y2="472" gradientUnits="userSpaceOnUse">
          <stop stop-color="#0B2948"></stop>
          <stop offset="0.58" stop-color="#063C63"></stop>
          <stop offset="1" stop-color="#075A80"></stop>
        </linearGradient>
      </defs>
    </svg>`;

  function installMark() {
    const logo = document.getElementById('jp-MainLogo');
    if (!logo) {
      return false;
    }
    if (!logo.classList.contains(MARK_CLASS)) {
      for (const className of Array.from(logo.classList)) {
        if (
          className.includes('JupyterIcon') ||
          className.includes('LogoIcon')
        ) {
          logo.classList.remove(className);
        }
      }
      logo.classList.add(MARK_CLASS);
      logo.innerHTML = MARKUP;
      logo.setAttribute('title', 'FleetDock IDEA');
      logo.setAttribute('aria-label', 'FleetDock IDEA');
    }
    return true;
  }

  if (!installMark()) {
    const observer = new MutationObserver(() => {
      if (installMark()) {
        observer.disconnect();
      }
    });
    observer.observe(document.documentElement, {
      childList: true,
      subtree: true
    });
  }
})();
