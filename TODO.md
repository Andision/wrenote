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
- [x] `data.dir` roots everything — DB, recordings, models, runtime packs —
      with a key per item to move just that (see "Data directory" below)
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

### d. In-place updates — the shell half of the update channel

The engine now says a newer version exists and the client offers a Download;
what the user gets is an installer to run by hand. The index (`latest.json`)
is already in Tauri's updater format precisely so this step is only the shell
side. Agreed to do; the first item is a human action, and nothing below it can
start until it is done.

- [ ] **Signing keys (you).** `npx tauri signer generate -w ~/.tauri/wrenote.key`
      → a minisign keypair. Private key + password become the repo secrets
      `TAURI_SIGNING_PRIVATE_KEY` / `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`; the
      public key is committed (it goes in `tauri.conf.json` below). Keep the
      private key somewhere durable: a lost key means every installed copy
      stops trusting new releases and needs a manual reinstall.
- [ ] `tauri build` then writes a `.sig` next to each installer and
      `packaging/release/make_latest.py` already carries it into `latest.json`
      — verify on the first tagged build that `signature` is non-empty
- [ ] `tauri-plugin-updater` in `Cargo.toml`, `.plugin(tauri_plugin_updater::Builder::new().build())`
      in `lib.rs`, `plugins.updater.pubkey` + `endpoints` (the index URL) in
      `tauri.conf.json`, `updater:default` in `capabilities/default.json`
- [ ] One more bridge call (`wrenoteDesktop.installUpdate()`): download,
      verify, install, relaunch, with progress relayed to the client. The
      client's Download button prefers it when present and falls back to
      `openExternal` (Electron, older shells, plain browser).
- [ ] macOS: the replaced `.app` must be signed and notarized or Gatekeeper
      refuses it — that is the "Waiting on hardware" checklist, so ship
      Windows in-place first if the Mac side is still open.

## Next

### Data safety

- [x] **Migrations.** `PRAGMA user_version` + an ordered `MIGRATIONS` list in
      `core/store.py`: one transaction per step, a `.v<N>.bak` copy before the
      first step touches an existing file, a newer file refused rather than
      guessed at. The one migration (0 → 1) is the pre-versioning catch-up; a
      new table or column is now one appended entry, and a test holds a
      migrated file to the same shape as a fresh one.
- [x] **Data directory.** `data.dir` in the config (default `~/.wrenote`);
      `data.db_path`, `data.recordings_dir`, `models.dir`, `compute.runtimes_dir`
      each default under it and can point elsewhere. `~/.wrenote/config.yaml`
      itself stays: it is where `data.dir` is read from. No UI for it yet —
      Settings could show `GET /v1/info`'s `paths` with "open folder" buttons.
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
- [x] **Update notice.** The engine reads `latest.json` from the latest GitHub
      Release (`core/update.py`, `GET /v1/update`), the client raises one toast
      and shows the state in Settings → General, and Download opens the
      installer in the system browser through the shell. A `v*` tag now
      publishes the Release and the index (`build-tauri.yml`).

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
