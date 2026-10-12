import electron = require('electron');
import { EventTypeMain, EventTypeRenderer } from '../eventtypes';

const { contextBridge, ipcRenderer } = electron;

type ShowProgressListener = (
  title: string,
  detail: string,
  showAnimation: boolean,
  renderRequestId: number
) => void;
type InstallBundledPythonEnvStatusListener = (
  status: string,
  message: string
) => void;

let onShowProgressListener: ShowProgressListener;
let onInstallBundledPythonEnvStatusListener: InstallBundledPythonEnvStatusListener;

contextBridge.exposeInMainWorld('electronAPI', {
  getAppConfig: () => {
    return {
      platform: process.platform
    };
  },
  isDarkTheme: () => {
    return ipcRenderer.invoke(EventTypeMain.IsDarkTheme);
  },
  onShowProgress: (callback: ShowProgressListener) => {
    onShowProgressListener = callback;
  },
  // [UAV-IDE MODIFY] Expose only the dedicated first-paint acknowledgement.
  notifyProgressViewPainted: (renderRequestId: number) => {
    ipcRenderer.send(EventTypeMain.ProgressViewPainted, renderRequestId);
  },
  sendMessageToMain: (message: string, ...args: any[]) => {
    ipcRenderer.send(message, ...args);
  },
  onInstallBundledPythonEnvStatus: (
    callback: InstallBundledPythonEnvStatusListener
  ) => {
    onInstallBundledPythonEnvStatusListener = callback;
  },
  copyToClipboard: (content: string) => {
    ipcRenderer.send(EventTypeMain.CopyToClipboard, content);
  }
});

ipcRenderer.on(
  EventTypeRenderer.ShowProgress,
  (event, title, detail, showAnimation, renderRequestId) => {
    if (onShowProgressListener) {
      onShowProgressListener(title, detail, showAnimation, renderRequestId);
    }
  }
);

ipcRenderer.on(
  EventTypeRenderer.InstallPythonEnvStatus,
  (event, result, message) => {
    if (onInstallBundledPythonEnvStatusListener) {
      onInstallBundledPythonEnvStatusListener(result, message);
    }
  }
);

export {};
