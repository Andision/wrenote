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
├── packaging/              PyInstaller specs, macOS Swift capture helpers, entitlements,
│                           model/runtime index builders, release/ (version + latest.json)
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

## Models (`engine/models.yaml`, `core/catalogue.py`)

Three separate concerns used to be one tangle: *what models exist* was a dict
keyed by filename in `core/models.py`, *which one to use* was a path in the
config that had to spell that same filename, and *how to run one* is the
backend. Only the third was ever in the right place.

* **What exists is data.** `engine/models.yaml` lists each model with an id,
  kind, backend, tier, size and sha256. `~/.wrenote/models.yaml` merges over it
  by id, so a user adds or replaces entries without touching code.
* **`size` and `sha256` are generated, not typed.**
  `packaging/models/refresh_catalogue.py` asks HuggingFace and rewrites those
  lines in place (an LFS object id is the file's sha256); `--check` fails on
  drift. It edits lines rather than re-dumping the YAML, because a round-trip
  would delete every comment in the file.
* **Config picks by id**: `stt.model: whisper-large-v3-turbo-q5`. Resolution is
  `params.model_path` (explicit path wins — the escape hatch, and what keeps
  every pre-catalogue `~/.wrenote/config.yaml` working) → `model:` id →
  the catalogue's default for that kind, if its backend matches.
* **Downloads verify the hash** before the atomic rename, and delete the partial
  on mismatch — `present` is a size check, so a wrong file of the right length
  would otherwise be trusted forever. Re-hashing gigabytes on every status poll
  is not an option, which is why the check lives at the end of the download.
* **Which model to offer is ranked against the machine.** `catalogue.options()`
  returns the same shape as `RuntimeManager.options()` — ordered, one
  recommended, reasons as codes — so the wizard and Settings render both the
  same way. The target tier comes from RAM plus *discrete* VRAM (with
  `n_gpu_layers=-1` the weights live on the GPU; an iGPU's VRAM is system RAM
  and is not counted twice). A model that doesn't fit stays listed with its
  blocker rather than disappearing.
* **A change applies as soon as it can.** STT and the translator are built per
  WebSocket session, so a new choice lands on the next session; chat and the
  diarization speaker are held by `ModelManager` and are swapped in place, old
  weights unloaded. `POST /v1/models/select` answers `applies: "now" |
  "next_session"` and never asks for a restart — claiming one trains people to
  restart for nothing.
* Adding a *backend* needs no new machinery: `core/registry.py` is already a
  factory, and a remote provider registers into it like a local one.

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
* Detection asks whether the *driver* can run a variant, not whether an SDK is
  installed: a CUDA pack ships cudart and cuBLAS, so what it needs from the
  machine is `nvcuda.dll` and a driver at least `MIN_NVIDIA_DRIVER` (R527, the
  floor for CUDA 12 minor-version compatibility) plus enough VRAM. Every
  platform reports an `AcceleratorNote` per variant — what was detected, or
  what blocks it — so a missing accelerator can be explained rather than
  silently absent.
* `options()` is the **offer**, as opposed to the fallback chain `candidates()`
  computes. It recommends the lightest accelerated variant the machine can run
  (`ACCELERATED`): on Windows the CUDA pack is ~20x the Vulkan one — NVIDIA's
  cuBLAS alone is ~550 MB — and for streaming whisper plus a small translation
  model Vulkan already captures most of the GPU win, so CUDA is offered as an
  explicit upgrade instead of a default.
* Switching runtimes only needs a restart once a native binding is imported.
  The backends import theirs lazily inside `load()`, so during first-run setup
  nothing has, and `reactivate()` redoes the routing in place;
  `can_reactivate()` reports which case applies and `POST /v1/compute/select`
  answers `restart_required` accordingly.
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
  `compute.accelerator` to `~/.wrenote/config.yaml` and applies it live when it
  still can. Two clients drive them: `SetupGate` (first run — pick a runtime,
  then download the models; it only asks when an accelerator is genuinely
  installable, so Macs and offline machines go straight to the models) and
  Settings → Compute for everything after.

**Status:** implemented and tested. `.github/workflows/build-runtimes.yml` has
built and smoke-checked real `cpu`, `vulkan` and `cuda` packs on Windows
runners (the CUDA pack is ~770 MB, almost all of it cuBLAS). VAD and the
speaker model stay on CPU (ONNX Runtime) by design.

## Localization (`clients/web/src/i18n/`)

* Locales are **data**: `src/i18n/locales/<tag>.json`, discovered with
  `import.meta.glob`. Adding a language is dropping a file in — the picker in
  Settings lists it from its own `$meta.name`, and no component changes.
* Human-facing text belongs to the **client**. The engine reports codes and
  facts (`AcceleratorNote("cuda", False, "driver_too_old", {"driver": "471.11"})`)
  and never a sentence to display; `lib/computeText.ts` turns those into words.
  Otherwise a second translation system grows engine-side and the two drift.
  The engine's English rendering (`NOTE_TEXT`, `OPTION_NOTE_TEXT`) stays for
  logs and for clients that don't localize, and is the fallback when the client
  has no message for a code yet.
* Language names (English, 中文, 日本語) are endonyms and are never translated.
  Relative times and dates go through `Intl`, not through message keys.
* `npm run check:locales` fails on a key used but missing, a locale out of sync
  with `en`, or a message nothing references. It runs in CI before the SPA
  build, because a missing key is invisible to whoever speaks the language the
  app was written in.

## Data (`core/config.py`, `core/store.py`)

Everything the app writes for the user is one SQLite file plus directories of
large files, and it is the user's only copy.

* **One root.** `data.dir` (default `~/.wrenote`) is where it all goes;
  `data.db_path`, `data.recordings_dir`, `models.dir` and
  `compute.runtimes_dir` default to subpaths of it and can each be pointed
  elsewhere — a small system drive is the usual reason, and models and
  recordings are the gigabytes. An empty key means "under the root", an
  explicit one wins, so every pre-existing config keeps its meaning. The
  resolution happens once, in a validator on `Config`, so consumers read
  absolute paths and `GET /v1/info` (`paths`) shows what the process uses.
  `~/.wrenote/config.yaml` itself cannot move: it is where `data.dir` is read
  from. Nothing carries a module-level default path any more — `Store` and the
  recording writer take theirs as a required argument, so a forgotten one is
  a TypeError rather than a file written next to the user's real library.
* **The schema is versioned.** `PRAGMA user_version` says what a file has.
  `SCHEMA` is the current shape and is what a new file gets outright; an
  existing file goes through `MIGRATIONS`, an ordered list of
  `(version, step)`. Each step runs in its own transaction with the version
  stamped inside it, so a crash mid-migration rolls back to the previous
  version instead of leaving a half-rebuilt table; a `.v<N>.bak` copy (via the
  backup API, so WAL content is included) is taken before the first step
  touches an existing file; a file newer than the code refuses to open. The
  first migration is the pre-versioning catch-up — a version-0 file may be in
  any of the shapes the old probe-and-patch code produced — and it is the last
  one that probes; the second (session status) is what every later one looks
  like: three `ALTER TABLE` lines. `test_store_migrations` holds a migrated
  file to the same columns and indexes as a fresh one.

## A session's life (`core/store.py`, `core/refine.py`, `core/segmentation.py`)

Live transcription is a compromise the user hears in real time: the VAD
decides where utterances end, and Whisper sees each one on its own. The
moment the recording stops, none of that applies — the audio is on disk in
one file, and Whisper does its best work on exactly that. So a session has a
lifecycle, persisted as `sessions.status` and shown by the client:

    recording ──stop──► ready ──(refine_after_stop)──► processing ──► ready
                                                              └──► failed

* **`processing` is a state, not a spinner.** After a recording stops (or
  when the user asks, `POST /v1/sessions/{id}/refine`), the whole recording
  goes through the same pass an upload gets (`core/batch.py`, one
  implementation for both) and the rows it produces replace the live ones —
  in one transaction, at the very end, so the user never sees an empty
  transcript. The list and the session carry `status`, `status_detail` (why
  a pass failed), `refined_at` and, while processing, the `job_id` to follow;
  the client shows a badge in the sidebar, a strip above the transcript with
  the pass's progress, and disables the other jobs on that session. A failed
  pass leaves the live rows and offers a retry. Speaker labels carry over by
  time overlap (a renamed colleague survives); text edits to the live rows
  do not — a fresh transcription is the point, and the client says so before
  a manual re-run. Jobs don't survive a restart, so start-up settles what a
  crash left: `recording` → `ready`, `processing` → `failed(interrupted)`.
* **Segments are cut where it hurts least.** A live segment that reaches
  `max_segment_ms` is cut at the quietest moment of its last few seconds
  (`find_cut_point`), and the remainder opens the next segment right away —
  no word is split, nothing is decoded twice. Whisper is told the tail of the
  previous segment's text (`context_tail`, ~200 characters, before the
  glossary in the prompt), so a sentence the VAD split still decodes as one
  thought. The translator sees the previous segment in the same source
  language along with each new one, in the live pipeline and in every batch
  path (`translate_one_for_segment(context=)`), so a pronoun or a dropped
  subject has something to refer to. Both are session parameters
  (`stt_context_chars`, `translate_context_segments`; 0 turns them off) and
  neither has been measured on real meetings yet — they are the standard
  practice, not a tuned result.

## Minutes (`core/minutes.py`)

A transcript is the record; the minutes are what people read afterwards.
The on-device chat model that already answers questions about a session is
asked, once, for a fixed document — summary, key points, decisions, action
items with owner and due date, open questions — as JSON, in a chosen
language, and the answer is kept in `session_minutes` (schema v3), one row
per language. The parse is lenient (fenced JSON, JSON in prose, or no JSON
at all, which becomes a summary-only document) because a small model does
not always comply. A transcript longer than the model's window is done in
~16k-character pieces and merged with one more call. Each row remembers the
transcript hash it was written from, so the client can say "the transcript
changed since" — the post-recording pass does exactly that. The document
renders to Markdown with headings in its own language, on its own
(`/minutes/markdown`) or ahead of the transcript (`/export?minutes=<lang>`).

## Updates and releases (`core/update.py`, `packaging/release/`)

* **The engine reports, the shell installs, the client renders.** On launch
  the client asks `GET /v1/update`; the engine fetches `latest.json` from the
  latest GitHub Release (at most once per six hours, and not at all while
  `update.check` is off — `POST /v1/update/check` is the user asking and
  ignores both), compares versions itself (semver-ish; an unparsable index is
  never "newer", so a typo upstream cannot nag every user), and answers with
  facts and codes: current, latest, `available`, this machine's installer
  URL, the release page, `error` as `unreachable` | `bad_index` | `no_index`.
  Nothing about the machine is sent; it is the same GET a model download
  makes. The client raises one toast and shows the state in Settings →
  General; Download leaves the WebView through `wrenoteDesktop.openExternal`
  (Electron `shell.openExternal`, Tauri `tauri-plugin-opener`, both scoped to
  web URLs), because a link would navigate the app itself away.
* **`latest.json` is the updater plugin's format** (`version`, `pub_date`,
  `platforms.<target>.{url,signature}`) plus `release_url`, so the in-place
  installer, when it comes, reads the same file — see `TODO.md`. A `.sig`
  next to an installer fills `signature`; until builds are signed it is empty
  and ignored.
* **A `v*` tag is a release.** `build-tauri.yml` checks the tag against the
  manifests *before* the build, builds both platforms, then a `release` job
  writes the index with `packaging/release/make_latest.py` (each installer
  placed by the name Tauri gives it; an empty or ambiguous set refused) and
  creates the Release. The rolling runtime-pack release is a prerelease so it
  never becomes "latest".
* **The version is written in six manifests** because each toolchain reads
  its own. `packaging/release/version.py set X.Y.Z` rewrites them all and
  `--check` (run by `checks.yml`) fails when one is left behind: an app that
  reports 0.1.0 over a release named 0.2.0 would tell every user to update to
  what they already run.

## The contract (`engine/contract/`)

* `openapi.json` — generated by `python -m wrenote.contract`; a test fails when
  it drifts from the code, and CI runs `--check`, so every route or schema
  change is an explicit, reviewed contract change.
* `ws-protocol.md` — the WebSocket handshake, PCM format, control messages,
  every server event and error code, and the persist-before-send guarantee.
* Everything is under `/v1` except `/health` (the shell's readiness probe).
  Breaking changes ship under `/v2` with `/v1` kept for a release.

## Checks (`.github/workflows/checks.yml`)

Everything that can fail without a Mac, a Windows box or a 40-minute compile
runs on every push: engine lint + 248 tests + the API-contract drift check, and
for the client types, lint, locale parity, 55 tests and the build. The
platform-specific packaging workflows stay slow and separate.

* **The frozen engine is smoke-tested** in `.github/actions/build-engine`: a
  bundle can be missing a data file or a hidden import and still *build*
  cleanly, because PyInstaller only finds out at run time. The action boots it
  the way a shell does (spawn, read the `WRENOTE_PORT=` handshake) and asks for
  `/health`, `/v1/models/status`, `/v1/compute/status` and the SPA at `/` —
  between them those touch the config, the catalogue, the hardware probe and
  the bundled static files.
* **`eslint-suppressions.json` baselines** the client findings that predate the
  gate (React 19's new hook rules, in components this work didn't touch).
  Anything new fails; the file only shrinks. It is a debt list, not an
  exemption — `TODO.md` tracks it.
* **Every manifest agrees on the version** (`packaging/release/version.py
  --check`), and a release build also checks its tag against them before
  spending twenty minutes compiling.
* **Ruff runs clean**, with three categories disabled and a reason in
  `pyproject.toml`: ambiguous-unicode (this app is *about* CJK text), SIM105
  (the `except` blocks carry comments `suppress()` has nowhere to put), and the
  `tests/_spike_*.py` research scripts.
* The **catalogue-vs-upstream** check runs weekly rather than per push: it talks
  to HuggingFace, and a push shouldn't fail because a third party is down.

## Rules that keep this true

1. The engine has one entry point and no UI. Shells only spawn it.
2. Platform code only behind `PlatformAdapter`, with capability flags;
   unsupported means "returns None / empty", never "raises".
3. Native inference bindings are imported lazily inside backends.
4. Clients contain no business logic; anything computed goes through the API.
   The mirror of that: user-facing *wording* belongs to the client, so the
   engine sends codes and facts, not sentences.
5. A route or event change is a contract change: regenerate `openapi.json`,
   update `ws-protocol.md`, and the tests will hold you to it.
6. A schema change is one appended entry in `MIGRATIONS` plus the matching
   edit to `SCHEMA`; never edit or reorder a step that has shipped. The user's
   library is the only copy, and the migration tests hold you to it.
7. Paths come from `Config`, never from a module constant; the tests are
   isolated the way a user would move their data — by setting `data.dir`.

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
   pack build scripts are done, and the Windows `cpu` (4 MB) / `vulkan`
   (37 MB) / `cuda` (779 MB) packs are published on the rolling `runtimes`
   release with their index. What remains is the driver-matrix check
   (NVIDIA, AMD, Intel) on real machines.
4. Native clients only where native matters (macOS menu bar / overlay),
   starting with one platform against the contract.
