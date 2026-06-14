// electron/main.js — P1 of the pywebview→Electron migration.
//
// The shell spawns the wrenote FastAPI server as a *sidecar* and points a
// BrowserWindow at its loopback URL. The React SPA is served by the server and
// loaded unchanged; the web UI talks to the backend over loopback HTTP/WS (no
// Electron IPC).
//
// P1 (this commit): the shell generates a per-launch auth token and hands it to
// Python via the WRENOTE_AUTH_TOKEN env var (the server cookies it onto the SPA
// at `/`, so no frontend change). Python binds an OS-assigned free port and
// prints `WRENOTE_PORT=<n>` on stdout; the shell parses it. Clean SIGTERM
// shutdown; mic permission scoped to media.
//
// Still deliberately P1: native window frame (chrome is P3) and the dev conda
// Python (the bundled PyInstaller binary is P2).
const { app, BrowserWindow, ipcMain, screen, session, systemPreferences } = require("electron");
const { spawn } = require("node:child_process");
const crypto = require("node:crypto");
const http = require("node:http");
const path = require("node:path");

const HOST = "127.0.0.1";
const READY_TIMEOUT_MS = 30_000;

// How to launch the server: in a packaged app, the PyInstaller-frozen
// `wrenote-server` binary bundled under Resources; in dev, the conda env via -m.
function serverCommand() {
  if (app.isPackaged) {
    const dir = path.join(process.resourcesPath, "wrenote-server");
    const exe = path.join(dir, process.platform === "win32" ? "wrenote-server.exe" : "wrenote-server");
    return { cmd: exe, args: [], cwd: dir };
  }
  const python = path.join(app.getPath("home"), "miniforge3/envs/wrenote/bin/python");
  return {
    cmd: python,
    args: ["-m", "wrenote.run_server"],
    cwd: path.join(__dirname, "..", "backend"),
  };
}

let pyProc = null;
let mainWindow = null;
let overlayWindow = null;
// Remember the last overlay form's size so reopening it within a session keeps
// the user's full/compact choice without a resize flash (the renderer also
// reconciles from localStorage on mount, covering the first open after launch).
let overlaySize = { width: 760, height: 184 };

// Spawn the server, hand it the token, and resolve with the port it reports.
function startServer(token) {
  return new Promise((resolve, reject) => {
    const { cmd, args, cwd } = serverCommand();
    console.log(`[wrenote] spawning: ${cmd} ${args.join(" ")} (cwd=${cwd})`);
    const proc = spawn(cmd, args, {
      cwd,
      env: { ...process.env, WRENOTE_AUTH_TOKEN: token },
      stdio: ["ignore", "pipe", "inherit"], // stdout: parse port; stderr: uvicorn logs
    });
    pyProc = proc;

    let buf = "";
    let settled = false;
    proc.stdout.on("data", (chunk) => {
      const s = chunk.toString();
      process.stdout.write(s); // echo so we don't lose server output
      buf += s;
      const m = buf.match(/WRENOTE_PORT=(\d+)/);
      if (m && !settled) {
        settled = true;
        resolve(Number(m[1]));
      }
    });
    proc.on("exit", (code, signal) => {
      console.log(`[wrenote] server exited code=${code} signal=${signal}`);
      pyProc = null;
      if (!settled) {
        settled = true;
        reject(new Error(`server exited before reporting a port (code=${code})`));
      }
    });
    proc.on("error", (err) => {
      if (!settled) {
        settled = true;
        reject(err);
      }
    });
  });
}

function stopServer() {
  if (pyProc) {
    console.log("[wrenote] terminating server");
    pyProc.kill("SIGTERM");
    pyProc = null;
  }
}

function probeHealth(url) {
  return new Promise((resolve) => {
    const req = http.get(url, (res) => {
      res.resume();
      resolve(res.statusCode === 200);
    });
    req.on("error", () => resolve(false));
    req.setTimeout(1000, () => {
      req.destroy();
      resolve(false);
    });
  });
}

async function waitUntilReady(url, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await probeHealth(url)) return true;
    await new Promise((r) => setTimeout(r, 200));
  }
  return false;
}

function createWindow(baseUrl) {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    title: "Wrenote",
    backgroundColor: "#0a0a0a",
    webPreferences: { preload: path.join(__dirname, "preload.js") },
  });

  const wc = mainWindow.webContents;
  wc.on("did-finish-load", () => console.log("[wrenote] window did-finish-load OK"));
  wc.on("did-fail-load", (_e, code, desc, url) =>
    console.error(`[wrenote] did-fail-load ${code} ${desc} ${url}`),
  );

  mainWindow.loadURL(baseUrl);
  mainWindow.on("closed", () => {
    mainWindow = null;
    closeOverlay(); // the overlay is a companion, not a standalone window
  });
}

// ---- Floating subtitle overlay ("desktop lyrics") -------------------------
//
// A frameless transparent always-on-top window showing the live transcript
// while recording. It loads the same SPA at #overlay; transcript data flows
// main window → overlay over a same-origin BroadcastChannel, so the only
// Electron involvement is window management (overlay:toggle / overlay:close
// from preload.js).

function createOverlayWindow(baseUrl) {
  const { workArea } = screen.getPrimaryDisplay();
  const width = Math.min(overlaySize.width, workArea.width - 40);
  const height = overlaySize.height;
  overlayWindow = new BrowserWindow({
    width,
    height,
    // Bottom-center of the work area — where subtitles belong.
    x: Math.round(workArea.x + (workArea.width - width) / 2),
    y: workArea.y + workArea.height - height - 24,
    minWidth: 200,
    minHeight: 56,
    frame: false,
    transparent: true,
    hasShadow: false,
    resizable: true,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    title: "Wrenote subtitles",
    webPreferences: { preload: path.join(__dirname, "preload.js") },
  });
  // "screen-saver" keeps it above regular windows on both platforms; on macOS
  // also follow the user across Spaces and over fullscreen apps.
  overlayWindow.setAlwaysOnTop(true, "screen-saver");
  if (process.platform === "darwin") {
    overlayWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  }
  overlayWindow.loadURL(`${baseUrl}/#overlay`);
  overlayWindow.on("closed", () => {
    overlayWindow = null;
  });
}

function closeOverlay() {
  if (overlayWindow) {
    overlayWindow.close();
    overlayWindow = null;
  }
}

// Bring the main window back to the foreground (used when the overlay closes —
// the overlay is what the user was watching, so restore the app behind it).
function showMainWindow() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  }
}

// Grant only microphone/camera for our own loopback page; the OS still gates
// real capture (macOS TCC + Info.plist + audio-input entitlement when packaged).
// Replaces pywebview's hand-written WKWebView delegate.
function wireMediaPermissions() {
  session.defaultSession.setPermissionRequestHandler((_wc, permission, cb) => {
    cb(permission === "media");
  });
  if (process.platform === "darwin") {
    systemPreferences.askForMediaAccess("microphone").catch(() => {});
  }
}

app.on("window-all-closed", () => {
  stopServer();
  app.quit();
});
app.on("before-quit", stopServer);

// Single instance — Electron built-in, replaces desktop.py's fcntl/msvcrt lock.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  app.whenReady().then(async () => {
    wireMediaPermissions();

    const token = crypto.randomBytes(32).toString("base64url");
    let baseUrl;
    try {
      const port = await startServer(token);
      baseUrl = `http://${HOST}:${port}`;
      console.log(`[wrenote] server port ${port}`);
    } catch (err) {
      console.error("[wrenote] could not start server:", err);
      app.quit();
      return;
    }

    if (!(await waitUntilReady(`${baseUrl}/health`, READY_TIMEOUT_MS))) {
      console.error("[wrenote] server not ready in time; opening window anyway");
    }

    ipcMain.handle("overlay:toggle", () => {
      if (overlayWindow) {
        closeOverlay();
        showMainWindow();
      } else {
        createOverlayWindow(baseUrl);
        // Get the app window out of the way — the floating overlay is meant to
        // sit over whatever the user switches to (a meeting, a video, …).
        if (mainWindow && !mainWindow.isDestroyed()) mainWindow.minimize();
      }
    });
    ipcMain.handle("overlay:close", () => {
      closeOverlay();
      showMainWindow();
    });
    // The renderer picks the form (full pill vs compact bar); resize the host
    // window to fit, keeping it pinned at its current bottom-center anchor so
    // it doesn't jump when switching forms.
    ipcMain.handle("overlay:resize", (_e, { width, height }) => {
      overlaySize = { width: Math.round(width), height: Math.round(height) };
      if (!overlayWindow) return;
      const b = overlayWindow.getBounds();
      const centerX = b.x + b.width / 2;
      const bottomY = b.y + b.height;
      overlayWindow.setBounds({
        x: Math.round(centerX - width / 2),
        y: Math.round(bottomY - height),
        width: overlaySize.width,
        height: overlaySize.height,
      });
    });

    createWindow(baseUrl);

    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow(baseUrl);
    });
  });
}
