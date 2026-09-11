# Taking the LLM out of the engine

**Status: done.** The engine loads no language model. `llama_server` is the
default for chat and translation, `openai_compatible` is there for a model
someone else runs, and `chat/llama_cpp.py` and `translator/llama_cpp.py` are
deleted along with every trace of `llama-cpp-python` in CI and packaging.

The migration this plan described — flip the defaults, keep the old backend
for a release, then delete — was collapsed into one change, deliberately:
the app has no users but its author, so there is no config in the world that
needs a landing place. Anyone reading this later and planning a similar
removal should assume they need the three steps.

## The decision

The engine stops loading language models in-process. It speaks
OpenAI-compatible HTTP to *whatever is answering*, and when the user wants a
local model, the engine starts and supervises `llama-server` itself and
talks to it the same way.

The point is not that HTTP is nicer. It is that **a local model and a remote
one stop being two different things** — one backend, one streaming path, one
set of bugs — and that a crash in native inference stops taking the engine
with it.

Speech recognition does **not** go this way. See "What stays embedded".

## Why

* **Crash isolation, which is the strongest reason.** A llama.cpp segfault
  today kills the engine, and the moment it is most likely to happen is
  during a recording. A subprocess that dies is a subprocess that dies.
* **Memory actually comes back.** `chat/llama_cpp.py` says as much in its own
  comment: choosing a *smaller* model raises memory use until the next
  restart, because releasing the old weights is the binding's business.
  Killing a process is not.
* **One code path** for a remote API, a local server, and a CLI shim (see
  `CLI_AGENT_BACKENDS.md`, which reaches the same conclusion from the other
  direction). Wrenote's code stops containing any vendor's name.
* **Swapping a model** becomes restarting a process instead of
  `ModelManager.replace_chat` under a lock.

## Why it is cheaper than it looks

Both call sites use **only** `create_chat_completion`
(`chat/llama_cpp.py:105`, `translator/llama_cpp.py:127`). No grammars, no
logit bias, no raw completions. That maps one-to-one onto
`/v1/chat/completions`, so the migration is mechanical rather than a
redesign. The glossary reaches the translator as prompt text, which travels
over HTTP unchanged, and `timeout_s` becomes an HTTP timeout.

## What it costs

1. **Packaging.** `llama-cpp-python` ships today inside the compute runtime
   packs — per-accelerator wheels, built in CI. `llama-server` is a
   per-accelerator *binary* instead. llama.cpp publishes prebuilt ones per
   backend, so they could be downloaded and sha256-verified with the
   machinery the model catalogue already has, which is plausibly *simpler*
   than the wheel builds (`TODO.md` describes those as delvewheel, Ninja and
   CUDA archs). But it is a migration, and it does not delete the packs.
2. **A second process to supervise.** Spawn, health-check, kill on exit —
   and kill when the *engine* dies, or a 2.5 GB `llama-server` is left
   behind. The shell already has an orphaned-engine item on its checklist;
   this adds a grandchild to it.
3. **A listening port.** 127.0.0.1, an ephemeral port, a token — the same
   thing the engine already does for itself.
4. **Two features, two servers.** `llama-server` hosts one model; chat and
   the translator are different models, so both features on means two
   processes. Memory is unchanged (there are two instances today), the
   process count is not.
5. **Compute selection grows a consumer.** Choosing CUDA over Vulkan today
   affects whisper and llama together, through one wheel. It would then also
   have to pick the matching server binary. Not hard; fiddly.

Latency is not on this list: localhost HTTP costs about a millisecond
against hundreds of milliseconds of generation.

## What stays embedded

**Speech recognition.** The live path is coupled to the VAD, to streaming
partials, to the per-segment language policy and to prompt context; it is
not a request/response shape. `whisper.cpp` and `sherpa-onnx` stay in
process, which means the compute runtime packs stay too — they would carry
whisper and onnxruntime rather than whisper and llama.

## The plan, in three shippable steps

1. **`openai_compatible` backend.** ✅ **Done.** Nothing else changed. It
   immediately serves a remote API, a user-run `llama-server`, and a CLI shim
   — which is also `TODO.md` item **b**, so this step was needed either way.
   What it turned out to be: `core/openai_compat.py` (one client, streaming
   and not), `chat/openai_compatible.py`, `translator/openai_compatible.py`,
   and `translator/prompt.py` — the translation prompt lifted out of
   `translator/llama_cpp.py` so both backends ask the same question. 45 tests
   against `httpx2.MockTransport`.

   Three things the writing settled that the plan hadn't:

   * **Loopback is the privacy line.** A `base_url` on 127.0.0.1 is still
     local inference and the app still says so; anything else and the
     pre-flight screen names the features whose text is sent away.
     `remote_slots()` is that rule, `GET /v1/models/status` carries it, and
     `PreFlight` renders it. This answers the third open question below:
     the claim did not have to change for the *available-but-off* case,
     only for the configured-remote one.
   * **`GET /v1/info` had to start redacting.** It hands the merged config
     to the client, and Settings → Developer is the first thing anyone
     pastes into a bug report. `Config.redacted_dump()` masks any key that
     is a credential; `api_key_env` — the *name* of an environment variable
     — deliberately still shows.
   * **A misconfigured endpoint must not stop the engine booting.** `load()`
     opens a connection pool and does not probe: a health check would cost
     a round trip on every start-up, and some shims serve
     `/chat/completions` and nothing else, so there is no probe that works
     everywhere. The probe exists, but as a button — `POST
     /v1/models/endpoint/test`, spent at the moment someone presses Save,
     which is when a typo in a URL or a stale key should surface rather
     than three days later mid-meeting.
   * **The endpoint could not live in `params`.** Found by writing the UI:
     `params` is the *local* backend's tuning, and one dict holding both
     meant that configuring an endpoint and then picking a local model
     again handed `base_url` to `LlamaCppChat.__init__`, which refused to
     build. `BackendConfig.endpoint` is its own section;
     `catalogue.resolve()` expands it for an HTTP backend exactly as it
     expands a catalogue entry's files for a local one, and the two are
     kept side by side so switching back and forth remembers both. That
     shape is also what step 2 wants: a managed `llama-server` is an
     endpoint the engine fills in rather than the user.
   * **The API key is write-only.** `GET /v1/models/status` reports
     `has_api_key`, never the key, so the form shows "saved" and an
     omitted field means "leave it" — the client cannot echo back
     something it was never given, and a save that cleared the key every
     time the model name changed would be worse than no UI.
2. **Managed local server.** ✅ **Done.** `core/llama_server.py` spawns it,
   waits for `/health`, hands the step-1 client its address, and kills it on
   the way out. `chat/llama_server.py` and `translator/llama_server.py` are
   thin over that. 20 tests run a real subprocess — a stand-in speaking the
   two endpoints the supervisor depends on — because whether the process is
   actually spawned, waited for and killed is the entire feature, and a mock
   would have tested the mock.

   What the writing settled:

   * **Not leaking 2.5 GB is the hard part, and a pid is not evidence.**
     Ordinary shutdown was easy; the case that leaks is the engine being
     killed outright. Each server records its pid, port and token, and the
     next start reclaims what it finds — but only after asking the recorded
     port for `/health` with the recorded token. Pids get reused, and
     killing a stranger's process because we crashed is worse than leaking
     one. Reclaiming runs on every engine start regardless of the
     configured backend: the config may have moved off `llama_server` since
     the run that leaked, and then nobody would ever collect it.
   * **The same catalogue entry, not a duplicate one.** `llama_server` and
     `llama_cpp` execute the same GGUF, so `catalogue.backend_can_run` lets
     one run the other's models. Otherwise `models.yaml` grows a second
     copy of every model and the settings panel asks a question — "in-process
     or supervised?" — that step 3 is about deleting.
   * **A supervised backend needs things only the config knows** (where to
     record state, where to look for a binary), and `resolve()` injects them
     on *every* branch. Found the hard way: `params.model_path` returns
     early, so a pinned path left the backend defaulting its state file to
     `~/.wrenote` — outside the data dir the config chose, and during a test
     run, inside the developer's real one.
   * **It gets a random `--api-key`.** Loopback is not on its own enough:
     any page in the user's browser can reach 127.0.0.1, and the thing on
     the other end is their GPU.
   * **The port is a hint, not a reservation, and that showed up as a flaky
     test.** `llama-server` binds its own port, so all the engine can do is
     find a free one and let go of it; something else can take it in the
     gap. A spawn that exits before it is ready is retried on a new port a
     couple of times — which is a real fix, not a test workaround: the same
     race is waiting on a busy machine.
3. **Delete `chat/llama_cpp.py` and `translator/llama_cpp.py`.** ✅ **Done**,
   in one change rather than the staged migration below, because the app has
   exactly one user. What went with them: `llama-cpp-python` from every CI
   install, `collect_dynamic_libs("llama_cpp")` from both PyInstaller specs,
   `llama_cpp` from `DEFAULT_PACK_MODULES` (so switching compute runtimes now
   stays free until something transcribes — a `llama-server` is an executable,
   not an import), and `_ALSO_RUNS` from the catalogue. `llama_cpp_tag` stopped
   being optional in CI: a build without a `llama-server` is an app that cannot
   answer anything.

   Verified afterwards on the author's own config and real weights: default
   backends resolve to `llama_server`, a real Qwen3-4B answers, and the
   process count goes 0 → 1 → 0.

   The staged version, for anyone doing this with users to protect:

   1. **Prove it** on every platform you ship to.
   2. **Flip the defaults** in `config.yaml` and `models.yaml`. Existing
      `~/.wrenote/config.yaml` files still name the old backend and are
      unaffected, which is the point of doing this first.
   3. **Keep the old backend for one release.** It is what every existing
      config says, and it is the only way back if the new one is bad on
      someone's hardware.
   4. **Leave the name resolving before deleting the code** — an alias, and
      keep `_ALSO_RUNS` for user-written catalogue entries. The registry
      raises on an unknown backend *inside the lifespan*, so a config naming
      a deleted one doesn't degrade a feature, it stops the engine booting.
   5. **Then delete the implementations.**

Each step is releasable and reversible on its own, which matters because
step 2 is the one that can go wrong on a platform none of us is holding.

## Open questions

* ~~Where the `llama-server` binaries come from.~~ **Answered: the compute
  runtime pack ships it.** That keeps the trust chain exactly where it already
  is — our own CI builds the pack's native code and publishes it to our own
  release, rather than a third party's binary being fetched onto the user's
  machine — and a pack is already per-accelerator, which is precisely what
  `llama-server` needs to be. It also costs almost nothing on the engine side:
  `RuntimeManager.activate` puts the pack's `bin/` on the PATH already (it has
  to, for CUDA's DLLs), so `shutil.which` finds it and no pack-aware plumbing
  reaches into the backend.

  **It is not the whole answer, because not every platform gets a pack.**
  macOS arm64 ships Metal *built in* (`BUILTIN_VARIANT`), so
  `build-runtimes.yml` builds Windows packs only and there is no pack for the
  binary to ride in. It goes in the app bundle instead, beside ffmpeg and the
  capture helpers — which needed no new mechanism either: `run_server` already
  prepends `_MEIPASS` to the PATH for ffmpeg's sake, so the same
  `shutil.which` finds it. Two routes, one rule: *the binary lives wherever
  that accelerator's native code already lives.*

  Three things that fell out of it:

  * `ZipFile.extractall` drops the Unix mode bits, so the pack's `bin/` came
    out 0644 — invisible while it held only shared libraries, fatal for a
    binary the engine has to exec. Unpacking now restores the executable bit,
    mirroring the read bits so a file nobody may read does not become one
    anybody may run.
  * The pack grows in the middle and shrinks at the end: today it carries both
    `llama-cpp-python` and a statically linked `llama-server`, which are the
    same llama.cpp twice. Step 3 removes the binding and the duplication with
    it — the pack ends up carrying whisper and a server rather than whisper
    and a library, as "What it costs" §1 predicted.

  **Neither build step has run.** Both exist and both are off by default, so
  nothing about today's builds changes: `build-runtimes.yml` gained a
  `llama_cpp_tag` dispatch input for the packs, and
  `.github/actions/build-engine` the same input for the bundled one. Which
  llama.cpp tag to pin is the one judgement left — it should match the
  llama.cpp vendored by the pinned `llama-cpp-python`, or the supervised
  backend and the in-process one are quietly different llama.cpps. The first
  run with a tag set is the test of both.
* ~~Whether the first run should start the server eagerly or on first use.~~
  **Lazy, by inheritance rather than by decision:** the chat backend already
  loads on first use (`ModelManager`), and the translator is built per
  session, so the existing lifecycles put the spawn in the right place
  without anything new. Worth revisiting only if the wait before the first
  answer turns out to be worse than the in-process load it replaced.
* **The translator starts a server per session.** Same shape as today —
  `llama_cpp` also loads its weights per session — so it is not a
  regression, but it is a process spawn plus a model load at the start of
  every recording, and it is the obvious thing to make long-lived once step
  3 removes the alternative.
* ~~Whether the privacy claim in the UI needs to change at all when the
  "remote" case is available but off.~~ **Answered by step 1: no.** The claim
  is a fact about the running config, not about what the build can do, so it
  is unchanged until a `base_url` points off-loopback — see above.

Two more, raised by step 1 and belonging to step 2:

* ~~Whether the managed server should be one process or two.~~ **Two,
  because a server hosts one model and the two slots are different models.**
  They are labelled (`llama-server-chat.json`, `llama-server-translator.json`)
  so each is reclaimed independently.
* **What a shim's tool calls should do.** A `base_url` pointed at an agent
  CLI may answer with tool calls rather than text. Today they are dropped:
  only `delta.content` is read. That is the right default and the wrong
  permanent answer — see the MCP item in `TODO.md`.
