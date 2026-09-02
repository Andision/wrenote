# Wrenote desktop shell (Tauri)

The native host for the Wrenote engine. It does four things and nothing else:

1. spawns `wrenote-server` (the PyInstaller-frozen engine) as a loopback sidecar
   and hands it a random per-launch `WRENOTE_AUTH_TOKEN`;
2. opens the main window at the engine's URL once `/health` answers
   (a bundled "Starting…" page is shown during the cold start);
3. hosts the always-on-top subtitle overlay window and exposes three commands
   (`overlay_toggle`, `overlay_close`, `overlay_resize`) that the web client
   reaches through `window.wrenoteDesktop` (`clients/web/src/lib/desktop.ts`);
4. single instance, and a clean engine shutdown on exit.

The UI is the web client served by the engine, loaded in the system WebView
(WKWebView on macOS, WebView2 on Windows), so the installer is a few MB plus
the engine. Nothing in here knows an API route.

```
shells/tauri/
├── package.json          `npm run dev` / `npm run build` (Tauri CLI)
├── placeholder/          the "Starting Wrenote…" page shown before the engine is up
└── src-tauri/
    ├── tauri.conf.json   app + bundle config
    ├── tauri.release.conf.json  overlay for `tauri build`: ships engine/dist/wrenote-server as a resource
    ├── capabilities/     IPC allowed for the loopback origin (overlay commands, dragging)
    ├── Info.plist        macOS usage strings (merged into the app's Info.plist)
    ├── icons/            placeholder icons — regenerate with `npx tauri icon <1024.png>`
    └── src/
        ├── lib.rs        app wiring, Shell state, single instance, exit
        ├── engine.rs     spawn / port handshake / health probe / stop
        └── overlay.rs    overlay window + the three commands
```

## Develop

```bash
# engine deps in some Python (see engine/README section in the root README)
export WRENOTE_PYTHON=/path/to/python   # optional; default: python3 / python on PATH
cd shells/tauri
npm install
npm run dev          # spawns `python -m wrenote.run_server` from ../../engine
```

Linux needs the WebKitGTK dev packages Tauri documents
(`libwebkit2gtk-4.1-dev libgtk-3-dev librsvg2-dev`); it is a development
target only — the engine has no Linux capture adapter yet.

## Package

```bash
# 1. freeze the engine (from the repo root)
pyinstaller packaging/wrenote_server.spec --distpath engine/dist --workpath packaging/build
# 2. bundle
cd shells/tauri && npm run build    # = tauri build --config src-tauri/tauri.release.conf.json
# → src-tauri/target/release/bundle/{macos,dmg,nsis}/
# The resource overlay lives in a separate file so `tauri dev` / `cargo check`
# work without a frozen engine (tauri-build rejects a missing resource dir).
```

CI does both in `.github/workflows/build-tauri.yml`.

## Validation checklist (needs real machines)

The Rust side compiles against Tauri 2 and the engine handshake is the same one
the Electron shell uses, but the WebView-specific parts can only be verified on
the target OS. Tick these before switching the release pipeline over:

- [ ] **macOS: microphone.** `getUserMedia` + AudioWorklet in WKWebView from the
      loopback origin. Requires the usage string (`Info.plist`), the
      `audio-input` entitlement (`packaging/entitlements.plist`) and the OS
      prompt. If WKWebView refuses capture, fall back to engine-side mic
      capture via the platform adapter (the WS protocol already accepts PCM
      from either side).
- [ ] **Windows: microphone.** Same in WebView2 (permission request handling).
- [ ] **Overlay:** transparent + frameless + always-on-top over a fullscreen
      app on macOS (`macOSPrivateApi` is on for transparency); on Windows over
      other windows; dragging from anywhere on the pill; resize between the
      full and compact forms keeps the bottom-center anchor.
- [ ] **Cold start:** placeholder page shows, then navigates once the engine is
      up; engine failure text appears in the window instead of a hang.
- [ ] **Exit:** closing the last window terminates the engine (no orphaned
      `wrenote-server`); second launch focuses the running instance.
- [ ] **macOS signing/notarization:** the PyInstaller onedir under
      `Resources/wrenote-server/` contains many Mach-O files; sign them
      individually (`codesign --deep` is not enough for nested unsigned
      dylibs) with the entitlements above before notarizing.
- [ ] **Windows installer:** NSIS per-user install; system-audio capture still
      works from the installed location (COM init on the loopback thread).

Until every box is ticked, `shells/electron/` stays the shipping shell and
`build.yml` keeps packaging it.
