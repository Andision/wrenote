# Using a local `claude` / `codex` CLI as the chat backend — investigation

**Status: investigation only.** Nothing here is implemented. Measured on
macOS 26 (2026-09-10) against `claude` 2.1.267 and `codex-cli` 0.153.4, both
already installed and logged in.

**The question.** A user who already pays for Claude Code or ChatGPT has a
capable model on their machine, authenticated, with no API key to paste. Can
Wrenote's chat and minutes use it instead of the 2.5 GB Qwen3-4B download?

**Short answer, revised.** Technically yes — both fit `wrenote/chat/base.py`'s
`ChatBackend` as a subprocess, and §2–§4 below are how. But the premise
mostly collapsed on two findings, so read §0 first:

* **Anthropic ended subscription access for third-party tools on 2026-04-04.**
  Using your Claude Pro/Max login from another program is no longer covered;
  it needs an API key or a usage bundle. The whole pitch was "no key to
  paste", and for Claude that pitch is gone.
* **Nobody who does this well spawns the CLI from inside their app.** The
  OpenClaw-shaped tools put an OpenAI-compatible HTTP shim in front of the
  CLIs. Wrenote should speak to that shim, not to a subprocess.

## 0. What the ecosystem actually does, and what changed

**The policy.** From 2026-04-04, Claude Pro / Max / Team subscriptions no
longer cover usage through third-party agentic tools — OpenClaw, OpenCode,
and any other harness that routes requests through Claude over OAuth. Users
need a pay-as-you-go usage bundle or the API directly; official API keys,
Bedrock and Vertex are unaffected. Wrenote driving `claude` would be exactly
such a harness. So the "reuse the login you already have" argument holds only
for Codex (whose terms this investigation did *not* establish either way).

Once an API key is needed anyway, an ordinary OpenAI-compatible HTTP adapter
— `TODO.md` item **b**, already on the roadmap — beats driving a CLI on every
axis: no flag-stability coupling, no subprocess lifecycle, no 15k tokens of
agent scaffolding per call (§3).

**The architecture.** OpenClaw's own Claude backend spawns exactly the
command line §2 measured, but adds the two things that make it viable:

* **A warm subprocess** kept alive across consecutive turns, and
* **`--session-id <uuid>`** to resume, so a turn is a turn rather than a cold
  start. This is the answer to the per-call scaffolding cost below.

It does *not* bundle a Codex CLI backend; Codex runs through the
`codex app-server` harness instead — which matches what §3 found, that
`codex exec --json` gives no incremental output.

Tools in that family (e.g. `claw-orchestrator`) then expose
**`POST /v1/chat/completions`**, OpenAI-compatible and streaming, in front of
whichever CLI. Which is the shape Wrenote should target: one adapter it wants
anyway, pointed at a `base_url` the user chooses. The CLI question becomes
the user's configuration rather than our code, and swapping Claude for Codex
for OpenCode costs us nothing.

**So the recommendation changed.** Not "write `claude_cli` and `codex_cli`
backends" (§6, now superseded) but "write the OpenAI-compatible adapter, and
document pointing it at a local CLI shim". §2–§5 stay because they are the
measurements behind that conclusion.

---

## 1. What the interface needs

`ChatBackend` (`engine/wrenote/chat/base.py`) is four things: `load()`,
`unload()`, `chat(messages) -> AsyncIterator[str]`, and `info`. A subprocess
backend maps cleanly:

* `load()` — verify the binary exists and is logged in. Cheap; no weights.
* `chat()` — spawn per call, parse the stream, yield text chunks.
* `unload()` — nothing to release.
* `info` — the CLI's version and the model it reports using.

The interface's one hard requirement is *"implementations must not buffer the
whole response; the UX depends on first-token-out being fast."* That is where
the two differ.

## 2. Claude Code

```
claude -p "<prompt>" --output-format stream-json --include-partial-messages \
       --verbose --model <id>
```

Emits newline-delimited JSON. The useful events:

| event | carries |
|---|---|
| `system` / `subtype: init` | session id, model, the tools it has |
| `stream_event` → `content_block_delta` → `text_delta` | **the text, incrementally** |
| `assistant` | each complete content block |
| `result` | the final text, `total_cost_usd`, `duration_ms` |

Measured: a one-word answer took **1455 ms** and **$0.0228** — thinking was
on, and produced a `thinking_delta` block before the text, which a chat
backend would have to skip.

**`--bare` and the subscription login are mutually exclusive.** `--bare` is
the flag you want for an embedded call — it skips CLAUDE.md discovery, hooks,
plugin sync, and auto-memory — but it also states that *"Anthropic auth is
strictly ANTHROPIC_API_KEY or apiKeyHelper"*, and OAuth and the keychain are
never read. Verified: with `--bare` the run returned
`"Not logged in · Please run /login"` and `error: authentication_failed`.

So an integration that uses the login the user already has cannot use
`--bare`, and has to narrow the context flag by flag instead:
`--allowedTools ""`, `--strict-mcp-config`, `--system-prompt`, no `--add-dir`.
Worth re-checking each release: the flag list is not a stable contract.

## 3. Codex

```
codex exec --json --ephemeral --skip-git-repo-check -s read-only "<prompt>"
```

Emits JSONL, but only at *item* granularity:

| event | carries |
|---|---|
| `thread.started` | thread id |
| `turn.started` | — |
| `item.completed` → `type: agent_message` | **the whole reply, at once** |
| `turn.completed` | token usage |

**No incremental output.** There is no `--include-partial`-style flag in
`codex exec --help`. So a Codex backend can stream at message granularity
(and per-turn, for a multi-turn answer) but not per token, which is a visible
regression against the local model for anything long.

Two other things the run showed:

* **It loads the user's config unless told not to.** The probe pulled up the
  user's Cloudflare MCP servers and failed them noisily on stderr
  (`rmcp::transport::worker: worker quit with fatal: ... AuthRequired`).
  `--ignore-user-config` is needed, alongside `--ephemeral` (no session files)
  and `--skip-git-repo-check` (Wrenote's data dir is not a repo).
* **The agent scaffolding is the cost.** A five-token answer billed
  **15,602 input tokens** — the harness's system prompt and tool definitions.
  That is per call, and a chat over a long transcript pays it on top of the
  transcript.

## 4. What this is, and isn't

These are **coding agents with tool access**, not chat models. For Wrenote's
use — answer questions about a transcript, write minutes as JSON — every tool
must be off, the sandbox read-only, and the working directory somewhere
harmless. An agent that decides to read a file or run a command mid-answer is
a correctness and a privacy problem, not a feature.

`--output-schema` (Codex) is interesting for the minutes path specifically:
`core/minutes.py` currently asks for JSON and parses leniently because a 4B
model wanders. A schema-constrained reply would remove that whole class of
failure — if the flag is honoured strictly.

## 5. Privacy — the deciding constraint

Wrenote's claim is that everything runs on the user's device, and the setup
screen says so. Sending a transcript to a CLI sends it to Anthropic or
OpenAI. `TODO.md` item **b** already sets the rule for third-party models:
*"strictly opt-in, and the privacy claim in the UI must change when it is
on."* A CLI adapter is the same thing with a different transport — it is
easier for the user (no API key) and heavier per call, but it is not more
local. The same rule applies, and the UI must stop claiming otherwise while
it is on.

## 6. If it gets built as a subprocess backend after all — superseded, see §0

1. `chat/cli_agent.py` with two registrations, `claude_cli` and `codex_cli`,
   sharing the subprocess plumbing and differing only in argv and the event
   parser.
2. Neither is a catalogue model: they need no download, so they belong in
   `chat.backend` with no `chat.model`, and `required_models` already skips a
   slot whose backend needs no file.
3. `load()` is the honest place for the two failure modes — binary missing,
   not logged in — reported as codes (`cli_missing`, `cli_not_logged_in`) so
   the client can say which and how to fix it.
4. The privacy notice, wherever the "runs on your device" claim appears
   (`setup.*Blurb`, and the pre-flight shield line).
5. Version-pin nothing, but assert the flags: a startup probe that runs
   `--version` and one trivial prompt is what catches an upstream flag
   rename before a user's first question does.

## 7. Not investigated

* **Whether OpenAI's terms allow an application driving `codex` on the user's
  behalf.** Anthropic's answer is now known and is no (§0); OpenAI's is not,
  and it decides whether the Codex half of this has a point.
* The Claude Agent SDK, which is the supported way to embed rather than
  driving the CLI, and would not depend on flag stability. Note it spawns the
  same CLI over the same stdio protocol underneath, so the policy in §0
  applies to it too.
* Latency on a real transcript — every measurement above is one trivial
  prompt, and the interesting number is time-to-first-token with 30k tokens
  of meeting in the prompt, against a *warm* session rather than a cold one.
* Whether a warm subprocess plus `--session-id` actually removes the
  scaffolding cost in §3, or only amortises it.

## 8. Sources

* Anthropic's third-party access change, 2026-04-04 —
  <https://dev.to/mcrolly/anthropic-kills-claude-subscription-access-for-third-party-tools-like-openclaw-what-it-means-for-3ipc>
* OpenClaw, CLI backends (the command line, the warm subprocess, the session
  args) — <https://docs.openclaw.ai/gateway/cli-backends>
* `claw-orchestrator`, an OpenAI-compatible endpoint over five agent CLIs —
  <https://github.com/Enderfga/claw-orchestrator>
