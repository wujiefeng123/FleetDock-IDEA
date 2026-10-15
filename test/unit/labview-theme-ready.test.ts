import { describe, expect, it, vi } from 'vitest';
import { runInNewContext } from 'vm';
import { LabView } from '../../src/main/labview/labview';
import { SettingType } from '../../src/main/config/settings';

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>(complete => (resolve = complete));
  return { promise, resolve };
}

function viewWith(sync = true): any {
  const view = Object.create(LabView.prototype);
  Object.assign(view, {
    _themeGeneration: 0,
    _isDisposed: false,
    _labUIReady: false,
    _wsSettings: {
      getValue: (key: SettingType) =>
        key === SettingType.syncJupyterLabTheme ? sync : 'light'
    },
    _setJupyterLabTheme: vi.fn().mockResolvedValue(undefined),
    _view: {
      setVisible: vi.fn(),
      webContents: {
        isDestroyed: () => false,
        executeJavaScript: vi.fn().mockResolvedValue(undefined)
      }
    }
  });
  return view;
}

describe('LabView initial theme readiness', () => {
  it('waits for theme completion before publishing readiness and revealing the view', async () => {
    const view = viewWith();
    const theme = deferred();
    view._setJupyterLabTheme.mockReturnValue(theme.promise);
    const ready = view._prepareInitialTheme();
    await Promise.resolve();
    expect(view._labUIReady).toBe(false);
    expect(view._view.setVisible).not.toHaveBeenCalled();
    expect(view._view.webContents.executeJavaScript).not.toHaveBeenCalled();
    theme.resolve();
    await ready;
    expect(view._labUIReady).toBe(true);
    expect(view._view.setVisible).toHaveBeenCalledWith(true);
    expect(view._view.webContents.executeJavaScript).toHaveBeenCalledWith(
      expect.stringContaining('data-jupyterlab-desktop-theme-ready')
    );
  });

  it('preserves the saved web theme when desktop synchronization is disabled', async () => {
    const view = viewWith(false);
    await view._prepareInitialTheme();
    expect(view._setJupyterLabTheme).not.toHaveBeenCalled();
    expect(view._labUIReady).toBe(true);
  });

  it('reveals the existing web theme when synchronization fails', async () => {
    const view = viewWith();
    view._setJupyterLabTheme.mockRejectedValue(new Error('Theme unavailable'));
    await view._prepareInitialTheme();
    expect(view._view.setVisible).toHaveBeenCalledWith(true);
    expect(view._labUIReady).toBe(true);
  });

  it('does not block the workspace indefinitely when a theme command stalls', async () => {
    vi.useFakeTimers();
    try {
      const view = viewWith();
      view._setJupyterLabTheme.mockReturnValue(new Promise(() => undefined));
      const ready = view._prepareInitialTheme();
      await vi.advanceTimersByTimeAsync(10000);
      await ready;
      expect(view._view.setVisible).toHaveBeenCalledWith(true);
      expect(view._labUIReady).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it.each(['dispose', 'navigate'])(
    'does not reveal a stale view after %s',
    async kind => {
      const view = viewWith();
      const theme = deferred();
      view._setJupyterLabTheme.mockReturnValue(theme.promise);
      const ready = view._prepareInitialTheme();
      if (kind === 'dispose') view._isDisposed = true;
      else view._themeGeneration++;
      theme.resolve();
      await ready;
      expect(view._view.setVisible).not.toHaveBeenCalled();
      expect(view._view.webContents.executeJavaScript).not.toHaveBeenCalled();
    }
  );

  it('awaits the renderer theme command itself', async () => {
    const view = viewWith();
    delete view._setJupyterLabTheme;
    const command = deferred();
    const dataset = { jpThemeName: 'JupyterLab Dark', jpThemeLight: 'false' };
    const execute = vi.fn(async () => {
      await command.promise;
      dataset.jpThemeName = 'JupyterLab Light';
      dataset.jpThemeLight = 'true';
    });
    view._view.webContents.executeJavaScript.mockImplementation(
      (source: string) =>
        runInNewContext(source, {
          document: { body: { dataset } },
          window: { jupyterapp: { commands: { execute } } }
        })
    );
    let finished = false;
    const changing = view
      ._setJupyterLabTheme('light')
      .then(() => (finished = true));
    await Promise.resolve();
    expect(execute).toHaveBeenCalledWith('apputils:change-theme', {
      theme: 'JupyterLab Light'
    });
    expect(finished).toBe(false);
    command.resolve();
    await changing;
    expect(finished).toBe(true);
  });

  it('also waits for theme attributes when the command resolves before CSS loading', async () => {
    const view = viewWith();
    delete view._setJupyterLabTheme;
    const dataset = { jpThemeName: 'JupyterLab Dark', jpThemeLight: 'false' };
    let changed!: () => void;
    const disconnect = vi.fn();
    const clearTimer = vi.fn();
    view._view.webContents.executeJavaScript.mockImplementation(
      (source: string) =>
        runInNewContext(source, {
          document: { body: { dataset } },
          window: {
            jupyterapp: { commands: { execute: async () => undefined } }
          },
          MutationObserver: class {
            constructor(callback: () => void) {
              changed = callback;
            }
            observe() {}
            disconnect = disconnect;
          },
          setTimeout: () => 1,
          clearTimeout: clearTimer
        })
    );
    let finished = false;
    const changing = view
      ._setJupyterLabTheme('light')
      .then(() => (finished = true));
    await new Promise(resolve => setImmediate(resolve));
    expect(finished).toBe(false);
    dataset.jpThemeName = 'JupyterLab Light';
    dataset.jpThemeLight = 'true';
    changed();
    await changing;
    expect(finished).toBe(true);
    expect(disconnect).toHaveBeenCalledOnce();
    expect(clearTimer).toHaveBeenCalledWith(1);
  });
});
