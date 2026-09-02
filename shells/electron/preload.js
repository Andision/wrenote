// Minimal desktop API for the SPA. The app deliberately talks to the backend
// over loopback HTTP/WS, not IPC — this bridge only covers window management
// the web platform can't do itself (the always-on-top subtitle overlay).
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("wrenoteDesktop", {
  toggleOverlay: () => ipcRenderer.invoke("overlay:toggle"),
  closeOverlay: () => ipcRenderer.invoke("overlay:close"),
  resizeOverlay: (width, height) =>
    ipcRenderer.invoke("overlay:resize", { width, height }),
});
