# Wrenote architecture

Wrenote is a local, real-time speech transcription + translation app with
speaker diarization, a floating subtitle overlay and a meeting-notes chat. All
inference runs on the user's machine.

The repository is organised around one rule: **the engine is the product;
everything else is a thin client or shell over a versioned loopback API.**
A new platform is an adapter plus a packaging recipe, never a fork of the repo.

```
wrenote/
├── engine/                 Python engine: FastAPI + WebSocket over loopback. Zero UI.
│   ├── wrenote/core/       pipeline, store, export, jobs, glossary, diarize, runtimes …
│   ├── wrenote/{stt,vad,translator,speaker,chat}/   pluggable inference backends (registry)
│   ├── wrenote/platform/   the ONLY place the engine knows which OS it runs on
│   ├── wrenote/api/, ws.py the /v1 HTTP resources and the /v1/ws session endpoint
│   ├── contract/           openapi.json + ws-protocol.md — the interface clients build against
│   ├── config.yaml         defaults (user overrides in ~/.wrenote/config.yaml)
│   └── tests/              behaviour + contract tests (run without native models)
├── clients/
│   └── web/                React SPA — the reference client. Served by the engine at "/".
├── shells/
│   └── electron/           desktop host: spawns the engine, owns windows/overlay/permissions
├── packaging/              PyInstaller specs, macOS Swift capture helpers, entitlements
├── docs/plans/             historical design/migration plans (paths pre-date this layout)
└── .github/workflows/      CI: builds the SPA, freezes the engine, packages the shell
```

## The three layers

### Engine (`engine/`)

A pure server. It never opens a window, never draws UI, and never checks
`sys.platform` outside `wrenote/platform/`.

* **One entry point.** `python -m wrenote.run_server` (frozen: `wrenote-server`)
  binds an OS-assigned loopback port, prints `WRENOTE_PORT=<n>` on stdout and
  serves. Any shell starts it the same way and hands it a per-launch
  `WRENOTE_AUTH_TOKEN`; the engine cookies that token onto the SPA and gates
  every API/WS call with it, so other local pages can't reach the microphone.
* **All business logic lives here.** Segmentation, re-segmentation (split /
  merge), inline edits, export (md/txt/srt/vtt), glossary, diarization, session
  catalogue, chat over a transcript — all engine-side, all reachable over the
  API. A client that computes any of this itself is a bug.
* **Inference is pluggable.** Each of STT / VAD / translator / speaker / chat is
  an abstract backend registered by name (`core/registry.py`) and chosen in
  `config.yaml`. `mock` backends exist for every kind so the whole engine, its
  API and its tests run without a single native library or model.
* **Native bindings are imported lazily** inside each backend's `load()`, never
  at module import. This is what lets the compute runtime be swapped (below)
  and lets CI and tests import the engine on a machine without them.

### Clients (`clients/`)

A client is a view over the contract in `engine/contract/`. The web client is
the reference implementation and ships inside every desktop build. Native
clients (SwiftUI, WinUI, …) are additive: they talk to the same `/v1` API and
`/v1/ws`, push the same 16 kHz mono s16le PCM, and need no engine changes.

The web client keeps one API base (`src/lib/api.ts`); nothing else in it knows
a URL. It reaches the engine over the same origin it was served from, so it
works under a dynamic port and under any shell.

### Shells (`shells/`)

A shell is the smallest possible native host: spawn the engine, wait for
`/health`, open a window at the loopback URL, provide what the web platform
can't (the always-on-top subtitle overlay, media permission prompts, tray,
menus). Today this is Electron (~300 lines). Replacing it with Tauri or a
per-platform WebView host touches nothing outside `shells/`.

## Platform adapter (`engine/wrenote/platform/`)

`get_platform()` returns the adapter for the running OS. Everything OS-specific
is a method on it, and every method has a safe "unsupported" default:

| concern                | macOS (`darwin.py`)                      | Windows (`win32.py`)                     | other (`generic.py`) |
|------------------------|------------------------------------------|------------------------------------------|----------------------|
| system-audio capture   | ScreenCaptureKit `syscap` helper         | WASAPI loopback (`soundcard`)            | none → mic only      |
| screen/window capture  | ScreenCaptureKit `screencap` / ffmpeg    | ffmpeg gdigrab + `EnumWindows`           | none                 |
| hardware probe         | Apple Silicon → `metal`                  | WMI + nvidia-smi → `cuda` / `vulkan`; NPU reported | nvidia-smi   |
| single-instance lock   | `fcntl`                                  | `msvcrt`                                 | `fcntl`              |
| bundled helper lookup  | `_MEIPASS` / `packaging/macos/`          | `_MEIPASS` / `packaging/windows/`        | —                    |

`GET /v1/info` exposes `platform.capabilities` (`system_audio`,
`screen_capture`, `window_capture`) so clients hide controls that can't work.

**Adding a platform** = one module here (start from
`class X(PlatformAdapter): name = "x"`), a `packaging/<x>/` recipe, and a CI
matrix row. Nothing in `core/`, `api/` or any client changes.

## Compute runtimes (`engine/wrenote/core/runtimes.py`)

Local inference has to cope with NVIDIA, AMD and Intel GPUs (and NPUs) on
Windows, and Metal on macOS, without shipping one installer per accelerator.

* The app bundle carries one **built-in** runtime per platform: `metal` on
  macOS arm64, `cpu` on Windows (`BUILTIN_VARIANT`, overridable with
  `WRENOTE_BUILTIN_RUNTIME` in a CI matrix row).
* Accelerated **runtime packs** (`cuda`, `vulkan`) are per-platform builds of
  `llama-cpp-python` + `pywhispercpp`, fetched on demand into
  `~/.wrenote/runtimes/<variant>/` like the models are. Vulkan covers NVIDIA,
  AMD and Intel GPUs with no user-installed drivers; CUDA is the faster option
  for NVIDIA. NPUs are detected and reported but not used — there is no ggml
  backend for them yet; when one exists it becomes another registered backend.
* `RuntimeManager` does **probe → select → ensure → activate**: rank variants
  by hardware (or the `compute.accelerator` pin), skip ones persisted as bad,
  pick the first installed, put it on `sys.path` before any native import.
  A backend that fails on a variant calls `mark_bad()`; the next launch
  degrades one step down the chain instead of crash-looping.
* `gpu_layers_for()` budgets VRAM on discrete GPUs (the three default models
  total ~4.2 GB) and leaves unified memory unbudgeted.
* `GET /v1/compute/status` returns hardware, candidate chain, active runtime
  and pack state for the settings UI.

**Status:** selection, persistence and activation are implemented and tested;
`ensure()` raises `RuntimeUnavailable` until CI publishes packs. VAD and the
speaker model stay on CPU (ONNX Runtime) by design.

## The contract (`engine/contract/`)

* `openapi.json` — generated by `python -m wrenote.contract`; a test fails when
  it drifts from the code, and CI runs `--check`, so every route or schema
  change is an explicit, reviewed contract change.
* `ws-protocol.md` — the WebSocket handshake, PCM format, control messages,
  every server event and error code, and the persist-before-send guarantee.
* Everything is under `/v1` except `/health` (the shell's readiness probe).
  Breaking changes ship under `/v2` with `/v1` kept for a release.

## Rules that keep this true

1. The engine has one entry point and no UI. Shells only spawn it.
2. Platform code only behind `PlatformAdapter`, with capability flags;
   unsupported means "returns None / empty", never "raises".
3. Native inference bindings are imported lazily inside backends.
4. Clients contain no business logic; anything computed goes through the API.
5. A route or event change is a contract change: regenerate `openapi.json`,
   update `ws-protocol.md`, and the tests will hold you to it.

## Roadmap (agreed direction)

1. ✅ Engine abstractions: platform adapter, compute runtime interface,
   versioned contract, repo layout.
2. Tauri shell replacing Electron (native window chrome, tray, menus, overlay
   via multi-window; PyInstaller onedir engine shipped as a resource, not
   `externalBin`). Validate `getUserMedia` / AudioWorklet in WKWebView and
   WebView2; fall back to engine-side mic capture via the platform adapter
   if a WebView can't. Keep `shells/electron/` until Tauri is green in CI.
3. CI builds and publishes runtime packs (`cpu`, `vulkan`, `cuda` for
   Windows); wire `ensure()` downloads and the settings UI.
4. Native clients only where native matters (macOS menu bar / overlay),
   starting with one platform against the contract.
