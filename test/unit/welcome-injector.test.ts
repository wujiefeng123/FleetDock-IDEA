import { describe, expect, it, vi } from 'vitest';
import { FleetDockWelcomeInjector } from '../../src/main/fleetdock/welcome-injector';

describe('FleetDockWelcomeInjector', () => {
  it('resolves readiness only after the welcome DOM is decorated', async () => {
    let domReady: (() => Promise<void>) | undefined;
    const insertCSS = vi.fn().mockResolvedValue('fleetdock-css');
    const executeJavaScript = vi.fn().mockResolvedValue(undefined);
    const webContents = {
      isDestroyed: vi.fn().mockReturnValue(false),
      isLoadingMainFrame: vi.fn().mockReturnValue(false),
      getURL: vi.fn().mockReturnValue(''),
      on: vi.fn((event: string, callback: () => Promise<void>) => {
        if (event === 'dom-ready') {
          domReady = callback;
        }
      }),
      once: vi.fn(),
      removeListener: vi.fn(),
      removeInsertedCSS: vi.fn(),
      insertCSS,
      executeJavaScript
    };
    const injector = new FleetDockWelcomeInjector() as any;
    injector._readAsset = (fileName: string) => fileName;

    let ready = false;
    const readiness = injector.attach(webContents).then(() => (ready = true));

    await Promise.resolve();
    expect(ready).toBe(false);
    expect(insertCSS).not.toHaveBeenCalled();

    await domReady?.();
    await readiness;

    expect(insertCSS).toHaveBeenCalledWith('fleetdock-welcome-extension.css');
    expect(executeJavaScript).toHaveBeenCalledWith(
      'fleetdock-welcome-extension.js',
      true
    );
    expect(ready).toBe(true);
  });
});
