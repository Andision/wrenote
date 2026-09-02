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
│   ├── tauri/              desktop host (system WebView): spawns the engine, windows, overlay
│   └── electron/           the shipping host until the Tauri checklist is green
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
menus). Two exist: `shells/tauri/` (Rust, system WebView, a few MB) is the
target; `shells/electron/` (~300 lines) ships until the Tauri validation
checklist in `shells/tauri/README.md` is ticked on real macOS and Windows
machines. Both expose the identical `window.wrenoteDesktop` surface to the web
client, so swapping them touches nothing outside `shells/`.

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

* Packs are zips built by `packaging/runtimes/build_pack.py` (`MANIFEST.json`
  + `site-packages/` with the bindings installed `--no-deps` + optional
  `bin/` DLLs) and listed in a `runtimes.json` index
  (`packaging/runtimes/make_index.py`). `.github/workflows/build-runtimes.yml`
  builds `cpu` / `vulkan` / `cuda` for Windows and publishes them to the rolling
  `runtimes` GitHub release that `compute.runtimes_index_url` points at.
  `ensure()` downloads, verifies the sha256, checks the manifest (variant,
  platform, Python minor) and unpacks atomically.
* Activation routes only the pack's manifest-listed modules through a
  `sys.meta_path` finder placed ahead of PyInstaller's frozen importer — a
  plain `sys.path` entry would lose to the bundled copy.
* `POST /v1/compute/install` runs an install as a job (progress over
  `/v1/jobs/{id}/stream`); `POST /v1/compute/select` persists
  `compute.accelerator` to `~/.wrenote/config.yaml` for the next launch;
  Settings → Compute in the web client drives both.

**Status:** everything above is implemented and tested against packs built
locally; the CI workflow that compiles real CUDA/Vulkan packs on Windows
runners is written but has not been executed yet. VAD and the speaker model
stay on CPU (ONNX Runtime) by design.

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
2. 🔧 Tauri shell replacing Electron — written (`shells/tauri/`, engine
   sidecar as a bundle resource, overlay via multi-window, CI packaging job)
   and compiling against Tauri 2; awaiting the on-device checklist
   (`getUserMedia` / AudioWorklet in WKWebView and WebView2, overlay
   transparency, signing). Fall back to engine-side mic capture via the
   platform adapter if a WebView can't. `shells/electron/` ships until then.
3. 🔧 Runtime packs — engine install/activate, API, settings UI and the
   pack build scripts are done; `build-runtimes.yml` needs its first manual
   run (`workflow_dispatch`) to publish real Windows `cpu` / `vulkan` /
   `cuda` packs, then a driver-matrix check (NVIDIA, AMD, Intel) on real
   machines.
4. Native clients only where native matters (macOS menu bar / overlay),
   starting with one platform against the contract.
