# Wrenote

Local, real-time speech transcription and translation for meetings — with
speaker diarization, a floating subtitle overlay, transcript editing/export and
a chat over your notes. Everything runs on your machine by default, and
nothing leaves it unless you point translation or chat at a model endpoint
yourself (see "Your own model endpoint").

* **STT** whisper.cpp · **VAD** Silero · **Translation / chat** llama.cpp
  (Hy-MT2, Qwen3) · **Speakers** ECAPA-TDNN (ONNX)
* macOS (Apple Silicon, Metal) and Windows (CPU built in; CUDA / Vulkan
  runtime packs installable from Settings → Compute — see `ARCHITECTURE.md`)

## Layout

| path               | what                                                                     |
|--------------------|--------------------------------------------------------------------------|
| `engine/`          | Python engine (FastAPI + WebSocket on loopback). The product. No UI.     |
| `clients/web/`     | React web client — reference client, bundled into the desktop app        |
| `shells/tauri/`    | Desktop host (Rust + system WebView): spawns the engine, windows, overlay |
| `shells/electron/` | Previous desktop host; still the shipping one until Tauri is validated   |
| `packaging/`       | PyInstaller specs, macOS capture helpers (Swift), entitlements, runtime-pack builder |
| `engine/contract/` | `openapi.json` + `ws-protocol.md`: the API clients are built against     |
| `docs/plans/`      | historical design and migration plans                                    |

Read `ARCHITECTURE.md` for how the layers fit and the rules that keep new
platforms cheap.

## Develop

Engine (Python ≥ 3.11):

```bash
cd engine
pip install -e ".[dev]"
# native inference deps are per-platform and optional for dev; the mock
# backends run the whole engine without them. For real models see below.
python -m wrenote            # http://127.0.0.1:8000  (config: engine/config.yaml, ~/.wrenote/config.yaml)
pytest                       # 120+ tests, no models or native libs needed
ruff check .
python -m wrenote.contract   # regenerate engine/contract/openapi.json after API changes
```

Web client:

```bash
cd clients/web
npm install
npm run dev                  # http://localhost:5173, proxies /v1 and /health to :8000
npm run build                # → engine/static/app/, served by the engine at /
```

Desktop shell (needs the engine deps installed in a Python the shell can find;
set `WRENOTE_PYTHON` to point at it, default `python3` / `python` on PATH):

```bash
cd shells/tauri            # Rust toolchain + Tauri CLI; see shells/tauri/README.md
npm install
npm run dev
```

The Electron shell (`shells/electron`, `npm start`) remains until the Tauri
validation checklist is complete.

Real models: install `pywhispercpp` and `llama-cpp-python` for your platform
(CI pins the exact wheels in `.github/workflows/build.yml`), then set the
backends in `~/.wrenote/config.yaml` (see `engine/profiles/mac-default.yaml`).
The interface ships in English and 简体中文, following your system by default
(Settings → General to change it). Adding a language means adding one JSON file
under `clients/web/src/i18n/locales/` — see `ARCHITECTURE.md`.

First run walks through setup: pick the compute runtime for your hardware
(recommended option pre-selected — see `ARCHITECTURE.md`), then download the
models into `~/.wrenote/models/` — the model sizes offered are ranked against
your hardware. Both are changeable later in Settings.

Everything Wrenote writes lives under `~/.wrenote/` — the session library
(`data.db`), recordings, models and runtime packs. `data.dir` in
`~/.wrenote/config.yaml` moves all of it (say, to a bigger drive), and
`data.db_path` / `data.recordings_dir` / `models.dir` / `compute.runtimes_dir`
move just one thing; the config file itself stays where it is. The library's
schema is versioned and migrated in place on upgrade, with a `.bak` copy taken
first.

On launch the app asks the release index once whether a newer version exists
and shows a notice if so; nothing about you or your machine is sent. Settings →
General has the switch and a "check now".

### Your own model endpoint

Translation and chat can reach a model over HTTP instead of loading one.
**Settings → Models** has the fields — an address, an optional model name and
key, and a "Test connection" that asks the endpoint one question before you
rely on it. The switch sits with that slot's downloadable models, because it
is the same choice: a file on this machine, or something at a URL.

Anything that serves `POST /v1/chat/completions` works — `llama-server`,
Ollama, LM Studio, vLLM, a hosted API, or a shim that puts that endpoint in
front of an agent CLI such as `claude` or `codex`, which is how the tools in
that family are built (see `docs/plans/CLI_AGENT_BACKENDS.md`). The CLI
question is then your configuration rather than Wrenote's code.

The same thing by hand, for a config you keep yourself (all the keys are
documented in `engine/config.yaml`):

```yaml
chat:
  backend: openai_compatible
  endpoint:
    base_url: http://127.0.0.1:8080/v1
    model: ""                       # "" = whatever the server serves
    api_key_env: OPENAI_API_KEY     # read from the environment, not this file
```

`endpoint:` is separate from `params:` so the two never mix: `params` is the
local backend's tuning, and switching back to a downloaded model keeps your
endpoint on record rather than handing `base_url` to llama.cpp. A key you set
in Settings is stored in `~/.wrenote/config.yaml` and never sent back to the
UI — the field says "saved" instead. `api_key_env` keeps it out of the file
entirely.

For a local model, `backend: llama_server` runs that same catalogue model as a
subprocess the engine starts and kills, instead of loading it in process — a
llama.cpp crash then costs you the answer, not the recording, and the memory
comes back when the process exits. The binary comes from the compute runtime
pack for your accelerator (`Settings → Compute`), whose `bin/` is already on
the PATH; you can also point `params.binary` at your own. Packs do not carry
one yet, so `llama_cpp` remains the default — see `engine/config.yaml` and
`docs/plans/LLM_OUT_OF_PROCESS.md`.

Speech recognition is not offered this way and is not going to be: the live
path is coupled to the VAD, to partials and to per-segment language policy, so
it stays in the engine. **Your audio never leaves your machine either way.**
What a remote endpoint receives is transcript text — and when `base_url` is
not on loopback, the screen you start a recording from says so, and names
which features send it. A model server on `127.0.0.1` is still local
inference, and the app still says that.

## Package

CI (`.github/workflows/build.yml`) builds the SPA, freezes the engine with
PyInstaller and packages the Electron app for macOS arm64 and Windows x64.
Locally, from the repo root:

```bash
pyinstaller packaging/wrenote_server.spec --distpath engine/dist --workpath packaging/build
cd shells/tauri && npm run build       # or: cd shells/electron && npm run dist
```

`build-tauri.yml` packages the Tauri shell the same way.

## Release

```bash
python packaging/release/version.py set 0.2.0   # every manifest, in one go
git commit -am "release: 0.2.0" && git tag v0.2.0 && git push --tags
```

The tag makes `build-tauri.yml` build both platforms, create the GitHub
Release with the installers, and attach `latest.json` — the index every
installed copy reads to learn the release exists. CI refuses a tag that
disagrees with the manifests.

## License

AGPL-3.0 — see `LICENSE`.
