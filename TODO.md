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

## Next — agreed priorities from the competitive review

Numbered as in that review; 1, 2, 3, 4, 8, 9 are the ones we keep.

- [x] **3. Re-transcribe from the recording.** A session now has a lifecycle
      (`recording → ready → processing → ready | failed`, persisted in
      `sessions.status`, schema v2). After a recording stops the whole WAV
      goes through Whisper again and replaces the live rows in one
      transaction; the client shows a sidebar badge, a strip above the
      transcript with progress, and a retry after a failure.
      `POST /v1/sessions/{id}/refine` is the manual trigger; the setting is
      "Re-transcribe after recording" (on by default). Speaker labels carry
      over by time overlap; text edits to live rows don't. Not yet measured
      on real hardware: how long the pass takes on an hour of audio next to
      a warm live pipeline (two Whisper contexts in memory for a while).
- [x] **2 (segmentation half). Cut where it hurts least.** A segment at
      the length cap is cut at the quietest moment of its last 4 s and the
      rest opens the next segment; Whisper gets the previous segment's tail
      as prompt context. Both are session parameters with an off switch
      (`stt_context_chars`, `max_segment_ms` unchanged). Wants an A/B on a
      real meeting: the prompt is the standard trick (whisper_streaming's
      200 chars) and can, on bad audio, make Whisper repeat itself.
- [x] **4. Translation context.** The translator sees the previous segment
      in the same language with each new one, live and in every batch path.
      Hy-MT reads a `Context:` block by design; still needs a look at real
      output for a small model translating the context along with the text.
- [x] **1. Meeting minutes.** Summary / key points / decisions / action
      items (owner, due) / open questions from the transcript via the chat
      model, one document per language (schema v3, `session_minutes`),
      shown in a right-hand panel next to the transcript, copied or saved
      as Markdown, and put ahead of the transcript in an export. Long
      transcripts go through the model in pieces and are merged. Untested
      on a real model: how well Qwen3-4B keeps to the JSON shape and how
      long an hour's transcript takes — the lenient parse and the
      per-part fallback exist for exactly that.
- [x] **2 (search half). FTS5 over segments** (schema v4, trigram so
      Chinese works; short queries fall back to LIKE), a search box at the
      top of the sidebar with hits grouped by session that open the session
      at the line, a paged session list (keyset cursor, "load older"), and
      the chat pulls the lines matching the question from the trimmed part
      of a long transcript. (The FTS write cost on partials is settled by
      item 9 below: partial rows are not indexed.)
- [x] **8. Mixed Chinese/English.** A session has a main language and the
      others that may come up ("Also spoken" chips under the language
      strip; the translation target is the default). Whisper's per-segment
      detection then chooses only among those — never Japanese for a
      Chinese speaker — and a secondary one wins only at ≥ 0.6 confidence;
      below that the segment is the main language. `core/lang.py
      LanguagePolicy`, `secondary_langs` in the WS start. The threshold is
      the smoke-test figure; a real bilingual meeting should tell whether
      it sits right. The whole-file pass still uses one language for the
      whole recording (whisper.cpp detects once per call); a per-chunk
      pass there is the next step if mixed sessions come out wrong.
- [x] **9. Long meetings.** The per-event costs that grew with the
      transcript are gone: the transcript's speaker turns are cached so a
      partial re-renders one memoised card, not every card; the timeline
      rail measures card offsets at most three times a second instead of
      per partial; the full-text index skips rows that are still partial
      (schema v5 recreates the triggers), so the one write-path cost that
      grew with the text is paid once per line; the store runs WAL with
      `synchronous=NORMAL`, so a live session's two or three commits a
      second no longer fsync each; and partial events reach the client
      before the write, not after. Still to measure on a real two-hour
      recording: DOM size with ~1500 cards (virtualising the list is the
      next step if scrolling degrades) and the WAV writer's steady state.

### Live recognition

- [x] **A streaming-native recogniser for the live path.** sherpa-onnx
      Zipformer (bilingual zh-en, int8, ~200 MB, CPU) as a second STT
      backend; the pipeline feeds it directly and takes its endpoints, so
      the VAD segmentation, the cut at the cap and the prompt context are
      all idle on that path. Whisper stays on the whole-recording pass
      (`stt_offline`). Verified here on the model's own test clips: real
      time on 4 CPU threads, partials only grow, endpoints land on pauses,
      mixed 中/English in one line. Not verified: accuracy on a real
      meeting against Whisper's partials, and how the endpoint rules feel
      (trailing silence = the "min silence" setting, cap = "max segment").
      Whisper remains the default until that comparison is made.
- [ ] Punctuation for the streaming path (sherpa-onnx's CT-Transformer
      zh-en model, ~300 MB) so live English isn't a wall of lower case; or
      accept it, since the post-recording pass rewrites everything.
- [ ] Glossary → sherpa-onnx hotwords (it supports them with
      modified-beam-search; the bilingual model ships a `bpe.model` for
      the token mapping). The glossary reaches Whisper as a prompt today
      and the streaming model not at all.
- [ ] Apple SpeechAnalyzer (macOS 26+) as a third live backend on the Mac
      shell — system-provided, streaming, Chinese-capable, free.

### Data safety

- [x] **Migrations.** `PRAGMA user_version` + an ordered `MIGRATIONS` list in
      `core/store.py`: one transaction per step, a `.v<N>.bak` copy before the
      first step touches an existing file, a newer file refused rather than
      guessed at. The one migration (0 → 1) is the pre-versioning catch-up; a
      new table or column is now one appended entry (v2, session status, is
      the first such), and a test holds a migrated file to the same shape as
      a fresh one.
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

- [x] **Search.** FTS5 over `segments` and a paged list — see item 2 above.

### Product

- [ ] **Speaker identity across sessions.** ECAPA embeddings are computed and
      discarded — no table stores them — so every meeting starts at
      "Speaker 1/2/3" and the user renames the same colleagues again. Voice
      profiles are a small addition to machinery that already exists.
- [ ] **`switch_lang`.** `ws.py:370` logs "requested but not implemented";
      changing the target language mid-session means stopping and restarting.
- [ ] **Per-app audio capture** — "record Zoom, not the browser". System audio
      today is the whole output mix (macOS: an SCK display filter with no
      exclusions in `syscap.swift`; Windows: WASAPI endpoint loopback via
      `soundcard`). Both OSes can do per-process. macOS: the same
      ScreenCaptureKit filter with `including: [app]` (13+, which is already
      the helper's floor) — an `--app <bundle-id>` flag on `syscap`; Core Audio
      process taps (14.2+) are the alternative that needs no screen-recording
      permission. Windows: WASAPI process loopback
      (`AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK`, include-process-tree so
      Zoom's helpers count; Windows 10 2004+ / 11 — confirm the floor on a
      real machine), which `soundcard` can't reach, so a small native helper
      exe on the `syscap` pattern under `packaging/windows/`. PreFlight
      already enumerates windows/apps for the screen picker; the audio-source
      picker reuses that list, and the choice travels in the WS `start` config
      as `audio_source: {type: "system" | "app", id}`. Linux stays mic-only.
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
