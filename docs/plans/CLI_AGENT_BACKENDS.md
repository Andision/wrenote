# Using a local `claude` / `codex` CLI as the chat backend — investigation

**Status: investigation only.** Nothing here is implemented. Measured on
macOS 26 (2026-09-10) against `claude` 2.1.267 and `codex-cli` 0.153.4, both
already installed and logged in.

**The question.** A user who already pays for Claude Code or ChatGPT has a
capable model on their machine, authenticated, with no API key to paste. Can
Wrenote's chat and minutes use it instead of the 2.5 GB Qwen3-4B download?

**Short answer.** Yes, both fit `wrenote/chat/base.py`'s `ChatBackend`
interface as a subprocess. Claude Code streams token-by-token; Codex does
not. Both carry a large per-call overhead that a chat model does not, and
both send the transcript to a third party, which is the part that decides
whether this ships and how.

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

## 6. If it gets built

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

* Whether either CLI's terms permit an application driving it on the user's
  behalf. **This needs answering before any of the above.**
* The Claude Agent SDK, which is the supported way to embed rather than
  driving the CLI, and would not depend on flag stability.
* Latency on a real transcript — both measurements above are one trivial
  prompt, and the interesting number is time-to-first-token with 30k tokens
  of meeting in the prompt.
