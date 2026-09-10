# Taking the LLM out of the engine

**Status: agreed in direction, not started.** This is the plan for the next
piece of work; nothing here is implemented.

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

1. **`openai_compatible` backend.** Nothing else changes. It immediately
   serves a remote API, a user-run `llama-server`, and a CLI shim — which is
   also `TODO.md` item **b**, so this step is needed either way.
2. **Managed local server.** The engine spawns and supervises
   `llama-server` and points the same backend at it. This is the actual
   destination: local and remote become indistinguishable. By now the HTTP
   path has been proven by step 1.
3. **Delete `chat/llama_cpp.py` and `translator/llama_cpp.py`**, once step 2
   has run on macOS, Windows and Linux.

Each step is releasable and reversible on its own, which matters because
step 2 is the one that can go wrong on a platform none of us is holding.

## Open questions

* Where the `llama-server` binaries come from: bundled per platform, or
  downloaded on demand like the model weights. Downloading an *executable*
  deserves more thought than downloading weights, even with the same
  checksum machinery.
* Whether the first run should start the server eagerly or on first use.
  Eager costs memory for a feature most sessions never touch; lazy costs a
  few seconds at the moment someone asks a question.
* Whether the privacy claim in the UI needs to change at all when the
  "remote" case is available but off. It should not — nothing leaves the
  machine until a `base_url` points somewhere else — but the wording has to
  make that legible, and `TODO.md` item **b** already requires the claim to
  change when a remote model *is* configured.
