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
- [x] Two bugs that made the locale files look broken: the whole `export.*`
      section never loaded (`import.meta.glob` hands back a module namespace,
      and `export` is a reserved word, so that key had no named export to be
      spread from — read `default` instead), and `settings.cat.models` was
      never written. Keys the UI composes at render are invisible to
      `check:locales`, which scans for literal `t("…")`; `runtimeKeys.test.ts`
      now pins every one the client owns, in every locale.
- [ ] Remaining engine-generated strings: job phase/log lines, WS error
      messages, HTTP `detail` payloads — same code+params treatment.
      `feature_off` and `session.refuse.*` are the pattern to follow.
- [ ] Date/duration formatting through the active locale everywhere
- [x] Settings copy rewritten and regrouped: categories are places to look
      (General / Recording / Glossary / Models / About, with Advanced over
      Tuning / Compute / Engines / Developer), not one per subsystem, and
      "experimental" marks the one setting it is true of rather than a
      section that would become a graveyard.

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
- [x] A model row is two tags — its tier (Rough / Balanced / Most accurate)
      and the memory floor from `requires`, which turns red and becomes the
      reason when the machine is short — not a paragraph. The paragraph
      served nobody: whoever knows the models reads the name, whoever doesn't
      isn't helped by prose. It survives as the row's tooltip. Model names
      are ASCII now too ("Zipformer streaming (zh-en, int8)"): a name that
      switches script mid-string reads as a slip in either UI language.
- [x] **Features you can decline.** A first run downloaded 4.3 GB because
      `required_models` walked every slot, and 2.5 GB of that is the chat
      model a transcript-only user never touches. `<slot>.enabled` in the
      config switches translation, minutes+chat, and speaker identification
      off; the wizard opens on that choice, because it decides what there is
      to download. A declined feature keeps its buttons — hiding them makes
      the feature undiscoverable — and the click offers the download instead,
      leading back into the flow with that feature pre-switched-on. The
      switches are in Settings → Models too. Speech recognition is not
      optional: the app is a transcriber. Not measured: whether skipping the
      chat model actually shortens a first run enough to notice, on a real
      connection.
- [ ] **An `openai_compatible` chat/translator backend, and then no
      in-process LLM at all** — the plan is `docs/plans/LLM_OUT_OF_PROCESS.md`.
      The engine stops loading language models itself and speaks HTTP to
      whatever answers, including a `llama-server` it starts and supervises
      for the local case, so a local model and a remote one stop being two
      different things. The strongest reason is crash isolation: a
      llama.cpp segfault today takes the engine down, and the likeliest
      moment for it is mid-recording. Speech recognition stays embedded.
      Three shippable steps in that doc; step 1 is this adapter, which is
      needed either way. Strictly opt-in for anything remote, and the
      privacy claim in the UI must change when it is on.
- [ ] ~~A local `claude` / `codex` CLI as the chat backend~~ — **folded into
      the item above.** Second pass (`docs/plans/CLI_AGENT_BACKENDS.md` §0)
      killed the premise twice over: Anthropic stopped covering third-party
      tools with Pro/Max/Team subscriptions on 2026-04-04, so "reuse the
      login you already have" no longer holds for Claude and an API key is
      needed anyway; and the tools that do this well (OpenClaw and
      relatives) don't spawn CLIs from inside the app — they put an
      OpenAI-compatible HTTP shim in front of them. So the work is the
      adapter above, plus a paragraph of documentation about pointing its
      `base_url` at such a shim. One thing still unanswered: whether
      OpenAI's terms allow driving `codex` on a user's behalf.

### c. Tests and CI/CD

The engine has 149 tests; `clients/web` has 10k lines of TypeScript and no test
script at all. Business rules are leaking into the client (e.g. when the setup
wizard may skip the runtime step) with nothing holding them.

- [x] Vitest in `clients/web` — 125 tests: the message lookup, the code→words
      renderers, `SetupGate`'s skip logic, `ModelPicker` with a mocked API,
      and the components the lint clean-up touched
- [x] Locale key parity in CI (`npm run check:locales`, run before the SPA build)
- [x] Lint in CI (`checks.yml`) — ruff clean; eslint clean with no baseline
      and `--max-warnings=0` (see below)
- [x] A smoke test that boots the frozen engine and hits `/health` + `/v1/...`
      in the packaging workflows, so a broken bundle fails CI, not the user
- [x] **`eslint-suppressions.json` is worked off and deleted** — all 27
      findings, taken a component at a time; CI now lints with no baseline
      and `--max-warnings=0`. What changed, and why each is a real fix and
      not a rule silenced: refs written during render became refs written in
      an effect (`App`) or plain closures (`GlossaryEditor`); the timeline
      rail no longer reads the DOM from a `useMemo` — a card's every segment
      id maps to its offset, and the rail's own height comes from a
      ResizeObserver, so the play marker and the hover preview are pure
      renders; state reset from an effect became state that cannot go stale
      — `ChatBody` is keyed on the session and the upload dialog's body only
      exists while it is open, so both reset by unmounting; the speaker chip
      seeds its draft where the edit starts; `RecordingTimer` starts its
      clock in an effect instead of calling `Date.now()` during render; the
      speaker-meter rAF loop re-schedules itself by name; the two cva variant
      tables and the language lists moved out of component files (Fast
      Refresh), as did the playback context. Tests: `ThemeToggle` (the mount
      gate it no longer needs), `GlossaryEditor` (blur saves what was typed),
      `UploadDialog` (each open starts clean).
- [ ] Component tests for the parts with real interaction left: `ComputePanel`
      (install → select → restart-required), `Transcript` editing, `ChatPanel`
- [x] **Developer mode** — five taps on the version line in Settings →
      General. The first-run flow, a slot with no model and a failed download
      were the least-tested screens in the app because reaching them meant
      `rm ~/.wrenote/models/*` and a restart; Settings → Developer now has a
      button for each, plus the paths this process writes to and the merged
      config a bug report needs. `DELETE /v1/models/{id}` is the one
      destructive act, and it is recoverable by construction.

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

Measurements for everything in this section: `docs/plans/TRANSCRIPTION_QUALITY.md`.


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
      Whisper remains the default until that comparison is made — except
      on a machine with no accelerator, where the wizard lists the Zipformer
      first (`defaults.stt_cpu`): whisper-small measured at half real
      time on four CPU cores with no partial ever finishing, against the
      Zipformer's real time and ~1 s to first text, at similar accuracy on
      the same four clips.
      **The comparison has now been made, and it is bad** — see
      `docs/plans/TRANSCRIPTION_QUALITY.md` for the numbers. The Zipformer
      is unusable on a real English meeting, and on a machine with an
      accelerator there is nothing for it to solve: whisper-large-v3-turbo
      answers 935 ms after each utterance, which is the best option
      available and not a fallback. Its catalogue note says so now. Open
      question: whether it, or SenseVoice, is worth anything on a real
      *Chinese* meeting — the only case left where either earns a place.
- [x] **FunASR streaming Paraformer (bilingual zh-en, int8, ~240 MB)** as
      a second streaming option, same backend. On the four bilingual test
      clips both models ship, fed in real time through the live pipeline
      (`tests/_compare_live_stt.py`), it lost to the Zipformer: English
      runs garbled ("零八二 the day day tomororrow" for "the day after
      tomorrow", "always o s" for "always always"), a Chinese sentence
      turned to "一般现代式对后面它实现些些商" where the Zipformer had
      "一般现在时对后面它时态写上", one clip split at a hesitation, and
      no token timestamps (utterance start falls back to the first chunk).
      Latency and speed are similar (first text ~1 s, ~10× real time).
      So it is an option, not the default; if a streaming model becomes
      the live default, the Zipformer is the one to measure against
      Whisper — on a real meeting, with `_compare_live_stt.py` and a
      reference transcript.
- [ ] **Punctuation and case restoration, as a separate step** — and now
      for the *offline* pass too, not just the streaming one. Whisper's own
      casing on a real meeting is bimodal and unstable (three minutes of
      lowercase, then correct, flipping mid-file), and none of the three
      knobs tried stabilised it; each only moved where the instability
      landed. See `docs/plans/TRANSCRIPTION_QUALITY.md` §2. What makes this
      tractable is that it must not change the words, so it can be verified
      by diffing the word sequence — build that guard first, then try
      sherpa-onnx's CT-Transformer (~300 MB) or the already-downloaded chat
      model under a "repunctuate, do not reword" constraint.
- [ ] Glossary → sherpa-onnx hotwords (it supports them with
      modified-beam-search; the bilingual model ships a `bpe.model` for
      the token mapping). The glossary reaches Whisper as a prompt today
      and the streaming model not at all.
- [ ] Apple SpeechAnalyzer (macOS 26+) as a third live backend on the Mac
      shell — system-provided, streaming, Chinese-capable, free, and no
      model to download or hold in memory. Caveat to establish first: it
      transcribes **per locale**, so a sentence that switches between
      Chinese and English mid-way is likely to come out as one of them.
      That is this app's central case, so a locale-based recogniser may be
      excellent for a single-language meeting and wrong for the meeting
      Wrenote exists for — check that before building it, not after. On a
      machine with an accelerator it competes with whisper-large-v3-turbo
      at 935 ms per utterance (`docs/plans/TRANSCRIPTION_QUALITY.md`), so
      the case for it is CPU-only Macs and memory, not accuracy.

- [x] **About, with the licences.** `GET /v1/about`: Wrenote's own AGPL, the
      models, the native libraries, the front end, and the engine's Python
      dependencies — those read from the installed distributions at request
      time so the list cannot go stale. `wrenote/credits.yaml` and the model
      catalogue carry the rest, and an entry that does not assert a licence
      gives only its upstream link.
- [ ] **Verify the licences that are asserted, once, before a public
      release.** `models.yaml` claims MIT for the Whisper weights and
      Apache-2.0 for Qwen3 and the ECAPA model; those are the base models'
      terms and the catalogue points at the *quantised* re-uploads, whose
      repos should be read to confirm they redistribute under the same. The
      three that assert nothing (both sherpa-onnx models, Hunyuan MT2) are
      already link-only and stay that way until someone reads them.

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
- [x] **A saved export goes somewhere you can name.** It was a blob download,
      which in a WebView lands where the app can neither choose nor name, and
      nothing was reported either way — the copy action had a toast, the save
      had silence. The engine writes it now (`POST
      /v1/sessions/{id}/export/save`, and the minutes equivalent) into
      `data.exports_dir` and answers with the absolute path; the toast names
      the file and offers to open the folder.
- [ ] **Library export/import.** Only per-session export exists
      (`GET /v1/sessions/{id}/export`, `…/export/save`). Local-first software
      owes the user a way to take everything with them.
- [ ] **Recording lifecycle.** 16 kHz mono s16le is ~115 MB/hour, kept forever
      in `~/.wrenote/recordings/` with no retention policy, no disk-usage view
      and no bulk cleanup. Heavy users lose tens of GB without knowing to what.

### Making the transcript better after the fact

- [ ] **An AI clean-up pass over a finished transcript.** Distinct from the
      punctuation item above, which must not change words: this one is
      allowed to, and that is exactly what makes it dangerous. A meeting
      transcript is a record of what people said, and a model that "fixes"
      a sentence into something the speaker did not say produces a
      confident, readable, wrong document — worse than the rough one,
      because nobody can tell by reading it.
      So the design question comes before the feature: what is the unit of
      correction, and how does a reader see what was changed? Candidates,
      cheapest and safest first:
      * **Terms only.** The glossary already biases Whisper's prompt; a pass
        that only substitutes known terms ("cloud fair" → "Cloudflare",
        which this exact recording got wrong repeatedly) is bounded,
        auditable, and needs no model at all beyond fuzzy matching.
      * **Suggestions, not edits.** The model proposes; the row keeps the
        original and shows the alternative until someone accepts it.
      * **A rewrite, with the original kept.** Only with a visible diff and
        a way back to what was recorded.
      What must exist either way: the original text stays in the database.
      A schema column, not a replacement.
      Measurements to inform it: `docs/plans/TRANSCRIPTION_QUALITY.md` —
      note in particular that a style prompt improved punctuation *and*
      corrupted words on the same audio, which is this feature's failure
      mode in miniature.
- [ ] **The same, live.** Much harder and probably second: a correction
      that arrives after the line is on screen has to change text the user
      already read, and the live path's whole promise (a decoded prefix
      never changes) is the opposite of that. Worth deciding whether live
      correction is wanted at all before building it.

### Talking to other tools

- [ ] **MCP.** Two different features share the name; decide which first.
      * **Wrenote as an MCP server** — expose the library to other agents:
        search transcripts, read a session, read its minutes. The engine
        already has all of it behind HTTP (`core/search.py`,
        `/v1/sessions`, `/v1/sessions/{id}/minutes`), so this is mostly a
        protocol adapter over what exists, and it is the direction that
        makes Wrenote useful *inside* someone's existing agent setup.
        Access is the real question, not the protocol: an MCP server is a
        way for another program to read every meeting the user has ever
        recorded, so it needs to be off by default, explicitly enabled, and
        clear about what it exposes.
      * **Wrenote's chat panel as an MCP client** — let the transcript chat
        call tools. Bigger, and it changes what the chat *is*; it also
        lands more naturally after `LLM_OUT_OF_PROCESS.md`, since a shim
        speaking OpenAI-compatible HTTP may already be doing tool calls.

### Findability

- [x] **Search.** FTS5 over `segments` and a paged list — see item 2 above.

### Product

- [ ] **A global hotkey that starts recording.** The moment you want to
      record is the moment the meeting starts, and at that moment Wrenote
      is not the focused window — Zoom is. Pressing a key without leaving
      the call is the whole feature.
      What it needs, in order:
      * `tauri-plugin-global-shortcut` in `Cargo.toml` and its permission
        in `capabilities/default.json`. On macOS this registers a Carbon
        hotkey and needs no Accessibility grant; media keys would.
      * **An event in the other direction.** Today `window.wrenoteDesktop`
        is client→shell only (toggle the overlay, open a URL). A hotkey is
        shell→client, which the bridge has no channel for.
        `withGlobalTauri` is already on, so Tauri's own event API is the
        cheap answer; Electron's preload would need an `ipcRenderer.on`.
      * **It must be configurable**, and a combination another app already
        owns must fail loudly. Registration returns an error; swallowing it
        gives the user a key that silently does nothing.
      * Starting with no pre-flight means starting with the remembered
        settings, which is already how PreFlight works — it edits the
        persisted settings directly — so there is nothing to invent there.
      Two questions to settle before building:
      * **Does it also show the overlay?** The floating subtitle window
        already exists and is the natural companion: press the key, the
        recording starts and a small window appears over the call, without
        Wrenote taking focus. That may be the actual feature, with "start
        recording" as its side effect.
      * **What happens when Wrenote is not running?** A hotkey needs a
        process. Making it work from cold means a menu-bar / tray presence
        and a login item, which is a larger decision about what kind of app
        this is — and probably the thing that makes the hotkey worth
        having at all.
      Scheduling note: this, the in-place updater (section **d**) and the
      native save dialog all need the same kind of change — a Tauri plugin,
      a capability, and a build to verify. Nothing in the current
      environment compiles Rust, so they are one trip, not three.
- [ ] **Speaker identity across sessions.** ECAPA embeddings are computed and
      discarded — no table stores them — so every meeting starts at
      "Speaker 1/2/3" and the user renames the same colleagues again. Voice
      profiles are a small addition to machinery that already exists.
- [ ] **`switch_lang`.** `ws.py:370` logs "requested but not implemented";
      changing the target language mid-session means stopping and restarting.
- [x] **Per-app audio capture** — "record Zoom, not the browser". Built as
      specified: `audio_source: {type: "system" | "app", id}` in the WS start
      config, the audio picker reusing the screen picker's window list, and
      the platform saying (`audio_scope`) whether it can filter at all so the
      client never offers a choice the engine would have to approximate.
      macOS is `syscap --app <bundle-id>` with an `including:` filter,
      matched across every process of the bundle. Windows is
      `packaging/windows/procloop.cpp`, built in CI and bundled when present.
      Also here: **the microphone became optional**, which needed a clock —
      the pipeline was driven by mic frames, so "system audio only" was a
      missing `SystemAudioPump`, not a missing checkbox.
- [ ] **Verify per-app capture on real hardware.** macOS: compiles and the
      argument path runs, but capture needs the Screen Recording grant, so
      the filter has never actually been exercised — check that a Zoom-scoped
      recording really excludes a browser playing video, and what happens
      when the app quits mid-recording. Windows: `procloop.cpp` **compiles
      in CI** (2026-09-10) and has never been *run* — nothing in this
      environment runs Windows. Check that it captures the target's process
      tree and nothing else, and confirm the Windows 10 2004+ floor.
      Core Audio process taps
      (macOS 14.2+) remain the alternative that needs no screen-recording
      permission. Linux stays mic-only.
- [x] **Update notice.** The engine reads `latest.json` from the latest GitHub
      Release (`core/update.py`, `GET /v1/update`), the client raises one toast
      and shows the state in Settings → General, and Download opens the
      installer in the system browser through the shell. A `v*` tag now
      publishes the Release and the index (`build-tauri.yml`).

### Engineering

- [x] **Icon-only buttons now name themselves.** `data-tip` drives the app's
      own tooltip layer and is not an aria attribute, so 36 buttons read as
      "button" to a screen reader. `lib/tooltip.ts` `iconTip(text)` sets both
      from one string, so the two cannot drift, and `npm run check:a11y`
      (CI, next to check:locales) fails on a new icon-only control with a
      tooltip and no name — it found the last two the manual sweep missed,
      both conditional play/pause icons. Only icon-only controls: on anything
      with visible text `aria-label` *replaces* that text, which turns a
      correct name into a worse one. Replace the script with
      `jsx-a11y/control-has-associated-label` when that plugin supports
      ESLint 10 — it peers on ^9 today, which is why this is ours.
- [ ] **A native Save dialog** for exports, once the Tauri shell is verified
      on-device. `data.exports_dir` plus a reported path is the answer that
      works in every shell and in a browser tab, but "choose where, now" is
      still a dialog: `tauri-plugin-dialog` + `tauri-plugin-fs`, a
      `wrenoteDesktop.saveFile()` bridge call, and the client preferring it
      when present. Not attempted yet because nothing here can compile Rust,
      and shipping an unverified plugin wiring would break the shell build.
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
- [ ] **Real logo**, then regenerate the icons. There are two unrelated
      placeholders, and neither is the brand: `clients/web/public/favicon.svg`
      is a purple (#863bff) bolt from somewhere else entirely, and
      `shells/tauri/src-tauri/icons/*` is a black rounded square with a
      blue-violet waveform. Brand is warm brown, `#9e6f45` (`index.css`
      `--color-brand-600`). One source SVG, then `npx tauri icon` for the
      shell set and the same file as the favicon.
- [ ] After merging: drop the temporary branch `push:` triggers from
      `build-tauri.yml` and `build-runtimes.yml` (both marked "Remove once
      merged")
