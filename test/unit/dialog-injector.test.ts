import { EventEmitter } from 'events';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { FleetDockDialogInjector } from '../../src/main/fleetdock/dialog-injector';

vi.mock('../../src/main/fleetdock/styles', () => ({
  readFleetDockCss: () => 'body:has(.jlab-dialog-body) { color: #292929; }'
}));

class DialogContents extends EventEmitter {
  url = 'data:text/html;charset=utf-8,dialog';
  destroyed = false;
  loading = false;
  getURL = () => this.url;
  isDestroyed = () => this.destroyed;
  isLoadingMainFrame = () => this.loading;
  insertCSS = vi.fn(async () => 'dialog-css');
  removeInsertedCSS = vi.fn(async () => undefined);
}

const settle = () => new Promise(resolve => setImmediate(resolve));
const attach = (injector: FleetDockDialogInjector, contents: DialogContents) =>
  injector.attach((contents as unknown) as Electron.WebContents);

describe('FleetDock dialog appearance lifecycle', () => {
  beforeEach(() => vi.clearAllMocks());

  it('styles an already loaded local dialog once even when attached repeatedly', async () => {
    const injector = new FleetDockDialogInjector();
    const contents = new DialogContents();
    attach(injector, contents);
    attach(injector, contents);
    contents.emit('dom-ready');
    await settle();
    expect(contents.insertCSS).toHaveBeenCalledTimes(1);
    injector.dispose();
    expect(contents.removeInsertedCSS).toHaveBeenCalledWith('dialog-css');
    expect(contents.listenerCount('dom-ready')).toBe(0);
  });

  it('does not style a remote JupyterLab document', async () => {
    const injector = new FleetDockDialogInjector();
    const contents = new DialogContents();
    contents.url = 'https://example.test/lab';
    attach(injector, contents);
    contents.emit('dom-ready');
    await settle();
    expect(contents.insertCSS).not.toHaveBeenCalled();
    injector.dispose();
  });

  it('styles a new local document after navigation without duplicating listeners', async () => {
    const injector = new FleetDockDialogInjector();
    const contents = new DialogContents();
    attach(injector, contents);
    await settle();
    contents.emit('did-start-navigation', {}, contents.url, false, true);
    contents.emit('dom-ready');
    await settle();
    expect(contents.insertCSS).toHaveBeenCalledTimes(2);
    expect(contents.listenerCount('dom-ready')).toBe(1);
    injector.dispose();
  });

  it('removes CSS that completes after the injector was disposed', async () => {
    const injector = new FleetDockDialogInjector();
    const contents = new DialogContents();
    let complete: (key: string) => void;
    contents.insertCSS.mockImplementation(
      () => new Promise(resolve => (complete = resolve))
    );
    attach(injector, contents);
    injector.dispose();
    complete!('late-css');
    await settle();
    expect(contents.removeInsertedCSS).toHaveBeenCalledWith('late-css');
  });

  it('cleans up listeners when a renderer is destroyed', async () => {
    const injector = new FleetDockDialogInjector();
    const contents = new DialogContents();
    attach(injector, contents);
    await settle();
    contents.destroyed = true;
    contents.emit('destroyed');
    expect(contents.listenerCount('dom-ready')).toBe(0);
    expect(contents.listenerCount('did-start-navigation')).toBe(0);
    injector.dispose();
    expect(contents.removeInsertedCSS).not.toHaveBeenCalled();
  });

  it('keeps dialogs usable when appearance injection fails', async () => {
    const injector = new FleetDockDialogInjector();
    const contents = new DialogContents();
    contents.insertCSS.mockRejectedValue(new Error('Renderer unavailable'));
    attach(injector, contents);
    await settle();
    contents.insertCSS.mockResolvedValue('retry-css');
    contents.emit('dom-ready');
    await settle();
    expect(contents.insertCSS).toHaveBeenCalledTimes(2);
    injector.dispose();
  });
});
