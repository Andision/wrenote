> **Historical planning document.** Paths refer to the pre-reorganisation layout (`backend/` → `engine/`, `frontend/` → `clients/web/`, `electron/` → `shells/electron/`, `backend/packaging/` → `packaging/`). See `ARCHITECTURE.md` for the current structure.

# Wrenote Backend Architecture Refactor — Plan v2

> v2 incorporates a `codex exec` design review of v1 (read-only). Codex caught several
> factual errors in v1; all were verified against the code and corrected here. Changed
> sections are marked **[v2]**. Phase 0 (safety net) is **already implemented and green**.

**Goal:** Fix the backend layering problems (god-file `server.py`, business logic in the
transport layer, no dependency injection, scattered platform branching) **without changing
runtime behavior**. Structural refactor, not a feature change.

**Hard constraint:** the project had almost no automated tests. So we build a behavior
safety net *first* and do not touch `server.py` until it's green.

---

## 1. Current state (corrected facts) **[v2]**

- `wrenote/server.py` is **1667 lines**: app construction, lifespan, CORS, **import-time**
  loopback-auth middleware (gated on `WRENOTE_AUTH_TOKEN` read once at import — server.py:349/368),
  static + SPA mounts, every HTTP route, the WebSocket endpoint, business logic (transcript
  snapshot, translation orchestration, chat prompt assembly), and direct persistence.
- `app = FastAPI(...)` is a **module global**; `desktop.py`/`__main__.py` start it via the ASGI
  string `"wrenote.server:app"`.
- **[v2 correction]** Lazy-load state is **on `app.state`** (`chat_loaded`/`chat_load_lock`/
  `diarize_loaded`/`diarize_load_lock`), *not* module globals. Still unstructured — folding into a
  `ModelManager` is the cleanup, but the premise "loose globals" was wrong.
- **[v2 correction]** Mount order: `/static` is mounted **before** the API routes (server.py:391);
  only the **SPA `/`** mount is last (server.py:1666). v1 said "static/SPA mounts last" — wrong.
- **[v2 correction]** `/info` exists (server.py:418) and was missing from v1's inventory. The full
  route surface is **33 entries** (now frozen in `tests/test_api_characterization.py`).
- **[v2 correction]** `_persist_event` **already wraps all DB writes in try/except and logs without
  re-raising** (server.py:1523-1524), and deliberately persists **before** sending so the client can
  re-fetch (server.py:1529-1531). So v1's Phase 3 premise ("a DB error tears down the session") was
  **false**. → **Phase 3 dropped.**
- `store: Store = request.app.state.store` (and `.config`) is repeated in ~25 endpoints.
- **[v2 risk]** Background jobs (diarize/translate/upload) `asyncio.create_task` runners that close
  over `request`/`app.state` (server.py:805/849/942) and outlive the request. A DI refactor must
  capture `store`/`cfg`/`registry` as locals *before* spawning, not read `request.app` inside.
- **[v2 risk]** Import side-effects: `stt/__init__.py` unconditionally imports `whisper_cpp`, which
  imports native `pywhispercpp` at module load (server.py import path → `pipeline`/registry →
  backends). A "mock config" doesn't avoid this; the *import* needs the native dep. Tests run in the
  `wrenote` conda env where it's installed; making backend imports lazy is a nice-to-have that would
  also unlock CI without native wheels.
- `_translate_one_for_segment` does an in-function `from .core.pipeline import _text_lang_override`
  to dodge coupling (server.py:230). A new `core/translation.py` must **not** depend on
  `core.pipeline` — move `_text_lang_override` to a neutral util first.

## 2. Target layout

```
wrenote/
  app.py                 # create_app(config=None, auth_token=None): lifespan, middleware, routers, mounts
  server.py              # thin shim: `app = create_app()` (preserves "wrenote.server:app")
  deps.py                # app-agnostic FastAPI deps: get_store, get_config, get_pipeline, get_models
  auth.py                # _token_from_request + install_loopback_auth(app, token); _origin_allowed
  api/
    sessions.py groups.py jobs.py models.py transcribe.py chat.py recordings.py   # APIRouters
  ws.py                  # websocket_endpoint + _send_event/_send_error (+ origin/auth gate)
  core/
    transcript.py        # build_transcript_snapshot + chat system-prompt assembly
    translation.py       # TranslationService (translate_one / candidates / translate_session)
    lang.py              # neutral home for _text_lang_override (moved out of pipeline)  [v2]
    model_manager.py     # ModelManager: the 4 lazy flags/locks + ensure_chat/ensure_diarize
    platform.py          # PlatformCapabilities — centralizes sys.platform branches
```
Dependency direction (strict): `api/*`, `ws.py` → `core/*`. Never reverse. `deps.py` stays
app-agnostic (no importing concrete app objects). Per-app locks/state are created in lifespan.

## 3. Phased execution **[v2 — rephased per review]**

### Phase 0 — Safety net ✅ DONE (this commit)
Implemented against the current global app (no factory dependency):
- `tests/conftest.py`: `client` fixture — injects a pure-mock config + temp DB/recordings, runs the
  real lifespan via `TestClient`.
- `tests/test_api_characterization.py`: **route-surface snapshot** (exact 33-entry (method,path) set),
  health/info/sessions/groups/jobs/models/recordings status+shape, group CRUD round-trip, and the
  **mount-ordering** tests OpenAPI can't see (`/sessions` returns JSON not SPA; `/` serves index.html;
  unknown deep link → **404**, pinned as current behavior — see finding below).
- `tests/test_ws_characterization.py`: origin gate, start→ready handshake + session persisted,
  bad/!start first-message error contract.
- `tests/test_auth_logic.py`: `_token_from_request` precedence + `_origin_allowed` (unit; the
  middleware itself is import-gated — see Phase 3).
- **Result: 24 new tests, full suite 35 passed, all four files ruff-clean.**
- **Finding (not a regression, a pre-existing gap):** `StaticFiles(html=True)` does not SPA-rewrite
  unknown paths → reload on a future client-side route would 404. Pinned, not fixed.

### Phase 1 — Extract business logic out of the transport layer ✅ DONE
- `core/lang.py` ← `text_lang_override` (was `_text_lang_override`, pure, formerly in pipeline.py);
  repointed pipeline.py (2 call sites) + the translation service. Breaks the pipeline↔server coupling.
- `core/transcript.py` ← `build_transcript_snapshot` + `build_chat_system_prompt` (snapshot + the chat
  system-prompt assembly, incl. the truncation note). Chat + title-suggest endpoints now call these.
- `core/translation.py` ← `translate_one_for_segment` / `has_real_translations` /
  `translation_candidates` / `translate_segments_for_session` (the offline (re)translation service).
- **server.py shrank ~158 lines; the 3 new modules are pure/I-O-light and ruff-clean.**
- Gate: **35 tests green**, route-surface snapshot unchanged, `import wrenote.server` clean.
- Note: server.py's pre-existing lint (SIM105/UP017/RUF006/I001 — unsorted import block, blind
  excepts, `datetime.timezone.utc`) is untouched; it predates this refactor and is for a later
  dedicated cleanup, kept out of the structural diff on purpose.

### Phase 2 — Routes → APIRouters, while server.py still owns the app **[v2]**
Foundation: `api/_common.py` holds the shared, no-cycle helpers — `safe_session_id` /
`safe_conversation_id` / `require_conversation` / `ensure_chat_loaded` / `ensure_diarize_loaded` /
`SAFE_SESSION_ID`. Routers import from `_common` + `core/*`, never from `server`.

- **2a ✅ DONE — leaf routers carved**: `api/sessions.py` (sessions CRUD), `api/groups.py`
  (groups + set-group), `api/recordings.py` (WAV get/delete), `api/jobs.py` (status + SSE),
  `api/models.py` (status + download). server.py `include_router`s them before the SPA mount.
  Helper defs + the 5 route families removed from server.py; usages repointed to `_common`.
  Gate: **35 tests green**, route-surface snapshot byte-identical, import clean.
  Note: `api/` carries 2 lint patterns copied verbatim from the original (B904 raise-from in
  recordings, RUF006 fire-and-forget task in models) — same debt as the un-carved endpoints,
  deferred to the lint pass; UP017/RUF100 were safe to fix in-place and were.
- **2b ✅ DONE — heavy routers**: `api/upload.py`, `api/translate.py`, `api/diarize.py`
  (+ speaker rename/assign), `api/chat.py` (conversations + chat + title-suggest). Phase-constants
  (`_TRANSLATE_PHASES`, `_DIARIZE_*`) and `_TITLE_SYSTEM` moved with their handlers.
  Backfilled `tests/test_api_carved_routes.py` (9 model-free 404/400 tests) so the carved handlers
  are *exercised*, not just registered. Gate: **44 tests green**, route snapshot byte-identical.
  **server.py: 1667 → 658 lines (−60%).** It is no longer a god file — it now holds only app
  assembly (lifespan, CORS, auth, mounts), the `/` `/health` `/info` trio, and the WS handler.
  `api/` carries 10 pre-existing lint patterns copied verbatim (SIM105/RUF006/B904, plus B008 which
  is the standard FastAPI `File(...)`/`Form(...)` idiom) — same debt as before, deferred to the
  lint pass; UP017/RUF100 fixed in place.
- **2c ✅ DONE — WS + auth extracted**: `wrenote/auth.py` (`AUTH_TOKEN`/`AUTH_COOKIE`/
  `token_from_request`/`origin_allowed`/`install_loopback_auth(app)`) and `wrenote/ws.py`
  (the `/ws` handler as a websocket `APIRouter`, with its own origin+token gate). server.py now
  calls `install_loopback_auth(app)` after CORS and `include_router(ws.router)`. `test_auth_logic.py`
  repointed to `wrenote.auth`. Gate: **44 tests green**, route snapshot byte-identical, imports clean.

**Phase 2 result: `server.py` 1667 → 187 lines (−89%)** — now a pure app-assembly module
(imports, lifespan, CORS, `install_loopback_auth`, router includes, `/` `/health` `/info`, SPA mount).
The ASGI string `"wrenote.server:app"` is unchanged, so `desktop.py`/`__main__.py` need no edits.
`ws.py` carries 3 pre-existing `SIM105` try/except/pass patterns verbatim (lint-pass bucket).

### Phase 3 — `create_app()` factory + ModelManager **[v2]**
- **3a ✅ DONE — factory**: `create_app(config=None, *, auth_token=None)` in server.py;
  `app = create_app()` preserves `"wrenote.server:app"`. `install_loopback_auth(app, token)` now takes
  the token as a param, so auth is **per-app, not import-gated**. Payoff: `tests/test_auth_middleware.py`
  (8 tests: 401 / public / bearer / query / cookie / wrong-token) — previously impossible. conftest
  migrated to build apps via `create_app(mock_config)` + an `auth_client` fixture.
- **3c ✅ DONE — ModelManager**: `wrenote/model_manager.py` folds the 4 loose
  `app.state.{chat,diarize}_{loaded,load_lock}` flags + the two `ensure_*` helpers into one class at
  `app.state.models`. Lifespan + chat/diarize routers rewired; `tests/test_model_manager.py` covers the
  503-when-disabled path + idempotent load. **Zero loose model-state refs remain.**
- **3b ✅ DONE — `Depends` DI**: `wrenote/deps.py` (`get_store`/`get_config`/`get_jobs`/`get_models`);
  every `api/*` endpoint now declares deps via `Depends` — **no `request.app.state.*` left in any
  router**, read-only endpoints dropped `Request` entirely. Added ruff `flake8-bugbear
  extend-immutable-calls` for `fastapi.Depends/File/Form/Query` so the DI idiom doesn't trip B008
  (also cleared the pre-existing `File(...)` B008). Gate: **55 tests green.**

**Phase 3 result (3 commits): `b60c173` split, `2b1a8a1` factory + ModelManager, `7cfcc79` DI.**
The backend refactor (Phases 0–3) is complete: god file gone, business logic in core, modular
api/auth/ws, factory + injectable auth + ModelManager, DI throughout. server.py 1667 → 176 lines.

### Phase 4 — Platform abstraction (additive)
- `core/platform.py` (`PlatformCapabilities`); migrate `sys.platform` from desktop/screenrec/syscap.
  Sets up the later windowing/title-bar work (out of scope here).

## 4. Out of scope **[v2]**
- **Persist-path hardening / async queue** — premise was wrong and a queue would change the
  persist-before-send guarantee. If ever wanted, a separate, independently-justified PR.
- Window chrome / cross-platform title bar (separate task).
- Frontend↔backend type generation; making backend imports lazy for native-free CI (nice-to-haves).

## 5. Risk + rollback
| Phase | Risk | Guard |
|---|---|---|
| 0 | done | additive; 35 green |
| 1 | low | pure moves; suite + snapshot |
| 2 | medium (route moves, mount order, import cycles) | route-surface snapshot + per-family commit |
| 3 | medium (factory + DI touch everything; job closures) | full TestClient coverage; real auth test added here |
| 4 | low | additive |
Rollback = `git revert` the phase commit; phases ordered to be independently revertible.
