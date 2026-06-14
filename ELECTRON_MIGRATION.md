# Wrenote Shell Migration — pywebview → Electron

**Goal:** Replace the pywebview native shell with an Electron shell, *without
touching* the React frontend, the FastAPI server, or the WS protocol. The
backend becomes a pure server (no window code); Electron owns the window,
process lifecycle, chrome, and (later) tray/menu/auto-update.

**Why Electron (not Tauri):** "native shell + local Python server as a sidecar
over loopback HTTP/WS" is the proven path for this class — JupyterLab Desktop,
ComfyUI Desktop, Datasette Desktop all do exactly this, all on Electron.
Tauri+Python-sidecar is the least-paved road (no famous shipping app uses it;
open macOS `externalBin` codesign/notarize bug `tauri#11992`; PyInstaller
one-file orphan-process kill problem). Electron's `child_process` + bundling +
notarize flow is the mature option for shipping a Python server.

---

## 1. What moves where

```
Electron main process (the shell)            Python sidecar (pure server)
├─ pick port + auth token                     └─ wrenote.server:app
├─ spawn backend ── --port/--token ─────────→     uvicorn on the given port
├─ wait /health → BrowserWindow(loopback URL)     (api/ ws/ auth unchanged)
├─ single-instance / mic grant / chrome
├─ (P4) tray / menu / auto-update
└─ kill sidecar on quit
        ↑ React frontend (frontend/) loaded unchanged over loopback HTTP/WS
```

- **Reused unchanged:** `frontend/` (React/Vite), `wrenote.server:app`, the WS
  protocol + all `api/` routers + `auth.py` + `ws.py`, most of
  `backend/packaging/wrenote.spec`, `entitlements.plist` (audio-input).
- **New:** `electron/` (main process + electron-builder config + updater).
- **Dropped/slimmed:** pywebview dep; `desktop.py:_grant_webview_media` (cocoa
  delegate hack); the hand-rolled single-instance lock; `webview.create_window`.
  `desktop.py` collapses to a thin "run the server" entry (`run_server.py`).

## 2. What Electron gives us for free (vs the pywebview hacks)

| pywebview hand-roll | Electron built-in |
|---|---|
| `fcntl`/`msvcrt` single-instance lock (`desktop.py:48`) | `app.requestSingleInstanceLock()` |
| subclass cocoa `BrowserView.BrowserDelegate` for mic (`desktop.py:99`) | `session.setPermissionRequestHandler` + `systemPreferences.askForMediaAccess` |
| no title-bar control (the "double bar" seam) | `titleBarStyle:'hidden'` (mac, keeps traffic lights) / `titleBarOverlay` (win) |
| — | `electron-updater` auto-update, native `Menu`/`Tray` |

## 3. Phased plan

### P0 — dev runs ✅ (this commit)
- `electron/package.json` + `electron/main.js`: spawn `python -m wrenote` (dev
  conda env), wait `/health`, open a `BrowserWindow` at `http://127.0.0.1:8000`.
  Native frame for now. Reuses a server already on :8000 if present; only kills
  what it spawned. Single-instance + permissive mic handler wired.
- **Gate:** window opens, frontend loads unchanged, record→transcribe works in dev.
- Fixed port / dev Python / native frame are all deliberate P0 shortcuts.

### P1 — sidecar hardening ✅ (this commit)
- New pure-server entry `wrenote/run_server.py`: binds an OS-assigned free port
  (socket handed to uvicorn, no TOCTOU), prints `WRENOTE_PORT=<n>` on stdout.
- `electron/main.js`: generates a per-launch token via `crypto.randomBytes`,
  passes it as `WRENOTE_AUTH_TOKEN` env (the server already reads it at import and
  cookies it onto `/` — so **the auth handshake needs no frontend change and no
  CLI token arg**). Parses the port from stdout; drops the fixed 8000 and the
  reuse-if-running shim (dynamic port + per-launch token make each launch
  self-contained).
- Clean shutdown: SIGTERM the child on quit/window-close; spawn failure → quit.
  Permission handler scoped to `media`.
- **Rollback-safe:** `desktop.py` (pywebview) and the pywebview dep are **kept
  intact**; `run_server.py` is additive. The old shell still runs until P2 drops
  pywebview.
- **Gate:** window opens on a random port, API/WS return 200 (token cookie
  handshake works), record→transcribe works in dev.

### P2 — packaging (in progress)
- **Separate server spec/entry** (rollback-safe; legacy `wrenote.spec` /
  `run_desktop.py` untouched): `backend/server_entry.py` + `backend/packaging/
  wrenote_server.spec` freeze the **pure server** — drops pywebview/`wrenote.desktop`,
  `console=True` for the stdout port handshake, onedir COLLECT (no BUNDLE). Keeps
  `collect_dynamic_libs` (pywhispercpp/llama_cpp/onnxruntime), static/app,
  config.yaml, silero_vad.onnx, ffmpeg, mac syscap/screencap helpers.
- `main.js` `serverCommand()`: packaged → spawn `Resources/wrenote-server/wrenote-server`;
  dev → conda `python -m wrenote.run_server`.
- electron-builder: `dir` target (mac `.app`), `extraResources` embeds the
  PyInstaller onedir, Info.plist mic/camera usage strings.
- **No Apple Developer account** → ad-hoc `codesign --sign -` only; notarization
  (clean distribution to others) deferred until an account exists. Local self-run
  works. Embedded native libs may need `cs.allow-jit` /
  `allow-unsigned-executable-memory` entitlements when signing.
- Milestone: a self-contained `.app` that runs without the dev env (native frame).

  Win `nsis` target + signing: later, alongside the eventual Apple account.

### P3 — chrome (kill the "double bar")
- Frameless/hidden title bar; merge `TopBar` into the title-bar strip with
  `-webkit-app-region: drag` regions. macOS: keep traffic lights via
  `titleBarStyle:'hidden'`, ~78px left inset. Windows: `titleBarOverlay` or
  self-drawn min/max/close (+ `WM_NCHITTEST` for Aero Snap).

### P4 — native ambitions
- Tray, native menu, `electron-updater` auto-update, notifications, deep links.

## 4. Risk + rollback
| Phase | Risk | Guard |
|---|---|---|
| P0 | low | additive; new `electron/` dir only, backend untouched |
| P1 | medium (port/token handshake, shutdown) | health-wait; reuse-if-running; onedir kill |
| P2 | medium-high (sign/notarize embedded Python) | mature electron-builder afterSign/notarize; reuse entitlements |
| P3 | medium (frameless OS-niceties: snap, drag, a11y) | ship P2 on native frame first; chrome is independent |
| P4 | low | additive |

Rollback: `desktop.py` + the pywebview path stay intact until P2, so the old
shell remains runnable for any phase prior to removing pywebview.
