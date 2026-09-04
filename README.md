# Wrenote

Local, real-time speech transcription and translation for meetings — with
speaker diarization, a floating subtitle overlay, transcript editing/export and
a chat over your notes. Everything runs on your machine; nothing leaves it.

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
First run walks through setup: pick the compute runtime for your hardware
(recommended option pre-selected — see `ARCHITECTURE.md`), then download the
models into `~/.wrenote/models/`. Both are changeable later in Settings.

## Package

CI (`.github/workflows/build.yml`) builds the SPA, freezes the engine with
PyInstaller and packages the Electron app for macOS arm64 and Windows x64.
Locally, from the repo root:

```bash
pyinstaller packaging/wrenote_server.spec --distpath engine/dist --workpath packaging/build
cd shells/tauri && npm run build       # or: cd shells/electron && npm run dist
```

`build-tauri.yml` packages the Tauri shell the same way.

## License

AGPL-3.0 — see `LICENSE`.
