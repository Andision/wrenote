# Window/Screen Capture — design + manual-test checklist

Feature: in the PreFlight screen, let the user **pick a specific window or display** to
record (video) alongside the existing audio capture; on stop, mux into an MP4.

**Status: implemented end-to-end** (helper built, backend + frontend wired, 57 backend tests
green, frontend builds). The capture *itself* is unverified — it's gated on macOS Screen-Recording
permission, which only the running app can obtain → see the manual checklist below.

Build the macOS helper: `swiftc -O -o screencap packaging/macos/screencap.swift -framework
ScreenCaptureKit -framework AVFoundation -framework CoreMedia -framework AppKit` (already built;
also bundled via `packaging/wrenote.spec`).

## Architecture
- **Enumerate** (`GET /capture/targets`) → `{displays:[...], windows:[...]}`.
  - macOS: `screencap --list` (ScreenCaptureKit) — Swift helper at `packaging/macos/screencap`.
  - Windows: `EnumWindows` (ctypes) for windows + display metrics.
- **Pick** in PreFlight (in-app list; thumbnails are a later polish).
- **Pass** the chosen target via the WS `start` config as `capture_target: {type, id, title}`.
- **Record** the target:
  - macOS: `screencap --window <id>` / `--display <id>` → silent H.264 MP4 (SCK + AVAssetWriter).
  - Windows: `gdigrab -i "title=<window title>"` (window) / `desktop` (display). *(WGC is the
    proper long-term API; gdigrab-title is the pragmatic v1 — see test notes.)*
  - Mux the silent video with the session audio WAV (existing `screenrec.mux`).

## Decisions
- In-app PreFlight picker (not the OS system picker). [user]
- Video + audio → MP4. [user]
- Helper targets macOS 12.3+ (SCK), matching `syscap.swift`'s 13+ floor.

---

## ⚠️ MANUAL TEST CHECKLIST (device/permission-gated — can't verify in CI/CLI)

### macOS — `screencap` helper
- [ ] **`screencap --list`** returns valid JSON of displays + windows once Screen Recording
      permission is granted. *(Blocked in the CLI by TCC `-3801 "user declined"`; needs the
      terminal/app to have Screen Recording permission.)*
- [ ] **Window capture** `screencap --window <id> --out x.mp4` produces a playable H.264 MP4 of the
      chosen window; AVAssetWriter session timing is correct (no 0-byte / unplayable file).
- [ ] **Display capture** `screencap --display <id> --out x.mp4` likewise.
- [ ] **Resolution**: capture is crisp on Retina (currently sized to the window's *point* size →
      may be soft; revisit using backingScaleFactor if blurry).
- [ ] **Stop**: closing stdin finalizes the MP4 cleanly (the `--out` file is valid after stop).
- [ ] **Permission prompt**: first run from the real app triggers the Screen Recording TCC prompt;
      capture works after granting. Sequoia/Tahoe re-consent (~monthly) is expected, no opt-out.
- [ ] **Code-signing**: the bundled `screencap` helper must be signed/notarized in the .app or TCC
      misbehaves (same as `syscap`).

### macOS — end-to-end
- [ ] PreFlight lists windows/displays; selecting one and recording yields an MP4 with the chosen
      window's video + the session audio, correctly muxed.

### Windows (cannot be tested on this Mac at all)
- [ ] Window enumeration (`EnumWindows`) returns sensible visible top-level windows w/ titles.
- [ ] `gdigrab -i "title=<title>"` captures the chosen window. Known gdigrab weaknesses to verify:
      occluded windows (may capture garbage), minimized windows (nothing), DPI scaling, and
      hardware-accelerated/DirectComposition windows (may be black). If these bite, upgrade to
      Windows.Graphics.Capture (WGC).
- [ ] Title collisions (two windows same title) — gdigrab picks one; acceptable for v1.

### Both
- [x] Build/bundle: `screencap` added to `packaging/wrenote.spec` like `syscap` (DONE — verify it's
      actually present in a fresh `.app` build).
