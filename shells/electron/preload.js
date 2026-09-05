// Minimal desktop API for the SPA. The app deliberately talks to the backend
// over loopback HTTP/WS, not IPC — this bridge only covers what the web
// platform can't do itself: window management for the always-on-top subtitle
// overlay, and opening a URL in the system browser.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("wrenoteDesktop", {
  toggleOverlay: () => ipcRenderer.invoke("overlay:toggle"),
  closeOverlay: () => ipcRenderer.invoke("overlay:close"),
  resizeOverlay: (width, height) =>
    ipcRenderer.invoke("overlay:resize", { width, height }),
  // The update download: a BrowserWindow would otherwise navigate the app away.
  openExternal: (url) => ipcRenderer.invoke("shell:openExternal", url),
});
