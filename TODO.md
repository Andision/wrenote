# TODO

Known gaps, in the order we agreed to work them. Each entry says *why* it
matters, not just what to build — a line item without a reason gets cargo-culted
or dropped for the wrong reason later.

See `ARCHITECTURE.md` for how the pieces fit and which rules constrain a fix.

## Now

### a. UI localization — done for the UI, engine strings remain

The app's whole point is Chinese ↔ English, and the interface was English-only.
Locales are data, not code: adding a language is "drop a JSON file in", with no
component touched. Human-facing text belongs to the client — the engine returns
codes and facts, never sentences to display.

- [x] i18n module, locale files auto-discovered, language picker in Settings
- [x] `en` + `zh-CN`, engine compute reasons as codes the client renders
- [ ] Remaining engine-generated strings: job phase/log lines, WS error
      messages, HTTP `detail` payloads — same code+params treatment
- [ ] Date/duration formatting through the active locale everywhere

### b. Models: configuration, not a hard-coded set

`core/models.py:_KNOWN` pins four models (~4.2 GB total). A four-core laptop
with no GPU has no way to run something smaller, and a user with a better
machine has no way to run something better. The first-run wizard already
probes the hardware for the runtime pick — the model tier is the other half of
that same question.

- [x] Model catalogue in data (`engine/models.yaml`), not a dict in the module,
      with `size`/`sha256` generated from upstream and verified on download
- [x] Config picks a model by id; `params.model_path` stays as the escape hatch
- [ ] `models.dir` is configurable now — do the same for recordings and the DB
      (see "Data directory" below)
- [x] Tiers (small / medium / large) recommended from the probed hardware,
      offered in `SetupGate` and in Settings → Models
- [ ] Adapters for common third-party APIs (OpenAI-compatible chat/completions,
      whisper-style transcription) so a user can point at a remote model —
      strictly opt-in, and the privacy claim in the UI must change when it is on

### c. Tests and CI/CD

The engine has 149 tests; `clients/web` has 10k lines of TypeScript and no test
script at all. Business rules are leaking into the client (e.g. when the setup
wizard may skip the runtime step) with nothing holding them.

- [x] Vitest in `clients/web` — 48 tests: the message lookup, the code→words
      renderers, `SetupGate`'s skip logic and `ModelPicker` with a mocked API
- [x] Locale key parity in CI (`npm run check:locales`, run before the SPA build)
- [x] Lint in CI (`checks.yml`) — ruff clean; eslint baselined so new findings
      fail (see below)
- [x] A smoke test that boots the frozen engine and hits `/health` + `/v1/...`
      in the packaging workflows, so a broken bundle fails CI, not the user
- [ ] **Work off `eslint-suppressions.json`** (15 files, 27 findings). These
      predate the gate and are mostly React 19's stricter hook rules —
      `react-hooks/refs` (reading a ref during render, 12×),
      `set-state-in-effect` (5×), `react-refresh/only-export-components` (5×).
      Each is a small refactor in a component this work didn't touch; doing
      them blind, bundled into "add CI", is how a working app breaks. Take them
      one component at a time, with a test.
- [ ] Component tests for the parts with real interaction left: `ComputePanel`
      (install → select → restart-required), `Transcript` editing, `ChatPanel`

## Next

### Data safety

- [ ] **Migrations.** `core/store.py` migrates with ad-hoc `_migrate_chat()` /
      `_migrate_groups()` — `try ALTER TABLE except`, plus a full table rebuild.
      No `user_version`, no ordering, no rollback. Two are survivable; the fifth
      is an incident. This is a local-first app: the user's only copy of their
      data is this file. Switch to `PRAGMA user_version` + an ordered migration
      list while there are still only two.
- [ ] **Data directory.** `~/.wrenote/` is hard-coded for models
      (`core/models.py`), recordings (`core/recording.py:22`) and the DB
      (`core/store.py:24`); only `runtimes_dir` is configurable. Users with a
      small system drive can't move several GB elsewhere.
- [ ] **Library export/import.** Only per-session export exists
      (`GET /v1/sessions/{id}/export`). Local-first software owes the user a
      way to take everything with them.
- [ ] **Recording lifecycle.** 16 kHz mono s16le is ~115 MB/hour, kept forever
      in `~/.wrenote/recordings/` with no retention policy, no disk-usage view
      and no bulk cleanup. Heavy users lose tens of GB without knowing to what.

### Findability

- [ ] **Search.** `store.list_sessions()` is `SELECT ... ORDER BY created_at
      DESC` with no limit and no filter, and there is no search route at all.
      After a couple of hundred meetings the only way to find anything is to
      scroll titles. SQLite FTS5 over `segments`, plus pagination on the list.

### Product

- [ ] **Speaker identity across sessions.** ECAPA embeddings are computed and
      discarded — no table stores them — so every meeting starts at
      "Speaker 1/2/3" and the user renames the same colleagues again. Voice
      profiles are a small addition to machinery that already exists.
- [ ] **`switch_lang`.** `ws.py:370` logs "requested but not implemented";
      changing the target language mid-session means stopping and restarting.
- [ ] **App auto-update.** Models and runtime packs each have an index,
      resumable download and checksum verification; the app itself has none.
      Tauri's updater plugin over the existing GitHub release channel.

### Engineering

- [ ] **Retire the dead shells.** `engine/wrenote/desktop.py` (pywebview, still
      an extra in `pyproject.toml`) and `shells/electron/` both linger next to
      `shells/tauri/`. Three shells means changes land in the wrong one. Delete
      two once Tauri passes the on-device checklist in `shells/tauri/README.md`.
- [ ] **Logs go nowhere.** `basicConfig` to stderr (`server.py:77`,
      `desktop.py:136`); a packaged app shows the user nothing and gives them
      nothing to send us. Write to `~/.wrenote/logs/` with rotation and add
      "open log folder" to Settings.
- [ ] **`auth.py` module-level token.** Read once at import, as its own
      docstring admits — one auth config per process, and tests have to reload
      the module to change it. Fold into `create_app(config, token)`.
- [ ] **CUDA pack is ~770 MB**, almost entirely NVIDIA's cuBLAS. Vulkan (36 MB)
      is the recommended default, so this is not urgent — but revisit if
      llama.cpp gains a way to link a slimmer BLAS.

## Waiting on hardware

- [ ] Tauri on-device checklist: `getUserMedia` / AudioWorklet in WKWebView and
      WebView2, overlay transparency, signing and notarization
      (`shells/tauri/README.md`)
- [ ] Runtime-pack driver matrix on real NVIDIA / AMD / Intel machines
- [ ] Real logo, then regenerate the placeholder Tauri icons (`npx tauri icon`)
- [ ] After merging: drop the temporary branch `push:` triggers from
      `build-tauri.yml` and `build-runtimes.yml` (both marked "Remove once
      merged")
