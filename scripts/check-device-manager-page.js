// [UAV-IDE MODIFY] Parse the embedded device-manager renderer script so
// template-string edits cannot ship a syntactically invalid onboarding page.

const assert = require('assert');
const { app, BrowserWindow } = require('electron');

app.whenReady().then(() => {
  const {
    DeviceManagerDialog
  } = require('../build/out/main/devicemanagerdialog/devicemanagerdialog.js');
  const parent = new BrowserWindow({ show: false });
  const manager = new DeviceManagerDialog({
    isDarkTheme: false,
    parent,
    workingDirectory: process.cwd()
  });
  const page = manager._pageBody;
  const match = page.match(/<script>\s*([\s\S]*?)\s*<\/script>/);
  assert(match, 'Embedded renderer script was not found.');
  new Function(match[1]);
  manager.window.window.close();
  parent.close();
  console.log('Device-manager onboarding page syntax checks passed.');
  app.quit();
});
