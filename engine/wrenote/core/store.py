"""SQLite-backed persistence for sessions, segments, chat and the glossary.

Single-file DB (``data.db`` under the data directory — see
:mod:`wrenote.core.config`). Async access via aiosqlite — the WS handler
upserts as transcripts/translations arrive so a crash mid-session still leaves
the partial work persisted. Cascade-delete is wired on the foreign key, but we
also explicitly remove the per-session WAV file (filesystem) from the same code
path so the two stay in sync.

Schema versioning
-----------------
The file header's ``PRAGMA user_version`` says which schema a file has.
``SCHEMA`` is the current one and is what a *new* file gets outright; an
existing file is brought up through ``MIGRATIONS``, an ordered list of
``(version, step)``. Each step runs in its own transaction and stamps its
version as part of that transaction, so a crash mid-migration rolls back to the
previous version instead of leaving a half-rebuilt table behind. Before the
first step touches an existing file, a copy is taken next to it
(``data.db.v<N>.bak``) — this is a local-first app and that file is the user's
only copy. A file newer than this code refuses to open rather than guess.

The frontend reads via the HTTP endpoints in :mod:`wrenote.api`; LocalStorage
is no longer authoritative.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite

log = logging.getLogger(__name__)

# Bump together with an entry in MIGRATIONS. A new file is created at this
# version straight from SCHEMA; test_store_migrations holds the two equal.
SCHEMA_VERSION = 3

# sessions.status — where a session is in its life:
#   recording   the WS session is live; segments arrive as the user speaks
#   processing  a job is rewriting the transcript from the recording (the
#               post-recording pass, or an upload being transcribed); the
#               rows on file stay readable until the job replaces them
#   ready       the transcript is what the user gets
#   failed      the last processing pass died; status_detail says why and the
#               previous transcript is still there
SESSION_STATUSES = ("recording", "processing", "ready", "failed")

# One statement per entry: a migration replays these inside its transaction,
# and executescript() would commit that transaction first. Every statement is
# idempotent (IF NOT EXISTS) because the pre-versioning catch-up below runs
# them against files that may already have any subset.
_BASE_TABLES: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS sessions (
        id           TEXT PRIMARY KEY,
        title        TEXT NOT NULL,
        created_at   TEXT NOT NULL,
        src_lang     TEXT NOT NULL,
        tgt_lang     TEXT NOT NULL,
        duration_s   REAL NOT NULL DEFAULT 0,
        group_id     TEXT,
        status       TEXT NOT NULL DEFAULT 'ready',
        status_detail TEXT,
        refined_at   TEXT
    )
    """,
    # Optional folders the sidebar groups sessions into. Membership is the
    # nullable sessions.group_id above; deleting a group just orphans its members.
    """
    CREATE TABLE IF NOT EXISTS session_groups (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        created_at  TEXT NOT NULL,
        position    INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS segments (
        session_id   TEXT NOT NULL,
        segment_id   TEXT NOT NULL,
        ord          INTEGER NOT NULL,
        started_at   REAL NOT NULL,
        ended_at     REAL NOT NULL,
        orig_text    TEXT NOT NULL DEFAULT '',
        orig_status  TEXT NOT NULL DEFAULT 'final',
        orig_lang    TEXT,
        trans_text   TEXT NOT NULL DEFAULT '',
        trans_status TEXT NOT NULL DEFAULT 'final',
        trans_lang   TEXT,
        speaker      TEXT,
        PRIMARY KEY (session_id, segment_id),
        FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_segments_session_ord ON segments(session_id, ord)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_created_at ON sessions(created_at DESC)",
    # A session can hold many chat threads ("conversations"). Messages hang off
    # a conversation, not the session directly.
    """
    CREATE TABLE IF NOT EXISTS chat_conversations (
        id          TEXT PRIMARY KEY,
        session_id  TEXT NOT NULL,
        title       TEXT NOT NULL DEFAULT '',
        created_at  TEXT NOT NULL,
        updated_at  TEXT NOT NULL,
        FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS glossary (
        id          TEXT PRIMARY KEY,
        term        TEXT NOT NULL,
        translation TEXT NOT NULL DEFAULT '',
        note        TEXT NOT NULL DEFAULT '',
        position    INTEGER NOT NULL DEFAULT 0
    )
    """,
    # Meeting minutes the chat model wrote for a session, one row per language.
    # `content` is the JSON document core/minutes.py defines; `transcript_hash`
    # is what it was written from, so a changed transcript shows as stale.
    """
    CREATE TABLE IF NOT EXISTS session_minutes (
        session_id      TEXT NOT NULL,
        lang            TEXT NOT NULL,
        content         TEXT NOT NULL,
        generated_at    TEXT NOT NULL,
        model           TEXT NOT NULL DEFAULT '',
        transcript_hash TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (session_id, lang),
        FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
    )
    """,
)

# Kept apart from _BASE_TABLES: the pre-versioning catch-up has to look at an
# existing chat_messages before deciding whether to create or rebuild it.
_CHAT_MESSAGES = """
    CREATE TABLE IF NOT EXISTS chat_messages (
        conversation_id TEXT NOT NULL,
        ord             INTEGER NOT NULL,
        role            TEXT NOT NULL,                  -- 'user' | 'assistant'
        content         TEXT NOT NULL,
        created_at      TEXT NOT NULL,
        PRIMARY KEY (conversation_id, ord),
        FOREIGN KEY (conversation_id) REFERENCES chat_conversations(id) ON DELETE CASCADE
    )
"""
_CHAT_MESSAGES_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_chat_conversation_ord ON chat_messages(conversation_id, ord)"
)

# The whole current schema — what a fresh file is built from.
SCHEMA = ";\n".join((*_BASE_TABLES, _CHAT_MESSAGES, _CHAT_MESSAGES_INDEX)) + ";\n"


# What a session row looks like to the API — list and get agree by construction.
_SESSION_COLUMNS = (
    "id, title, created_at, src_lang, tgt_lang, duration_s, group_id, "
    "status, status_detail, refined_at"
)


class StoreVersionError(RuntimeError):
    """The file was written by a newer Wrenote than this one."""


async def _user_version(db: aiosqlite.Connection) -> int:
    cur = await db.execute("PRAGMA user_version")
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def _columns(db: aiosqlite.Connection, table: str) -> set[str]:
    """Column names of ``table``; empty when it doesn't exist."""
    cur = await db.execute(f"PRAGMA table_info({table})")
    return {row["name"] for row in await cur.fetchall()}


async def _rebuild_legacy_chat(db: aiosqlite.Connection) -> None:
    """Rebuild a chat_messages keyed by ``session_id`` into the conversation model.

    Each session's existing messages are wrapped in one migrated conversation
    so no chat history is lost.
    """
    log.info("migrating chat_messages to the conversation model")
    cur = await db.execute("SELECT DISTINCT session_id FROM chat_messages")
    session_ids = [row["session_id"] for row in await cur.fetchall()]

    conv_for: dict[str, str] = {}
    for sid in session_ids:
        cur = await db.execute(
            "SELECT MIN(created_at) AS first, MAX(created_at) AS last "
            "FROM chat_messages WHERE session_id = ?",
            (sid,),
        )
        row = await cur.fetchone()
        first = (row["first"] if row else None) or ""
        last = (row["last"] if row else None) or first
        conv_id = uuid.uuid4().hex
        conv_for[sid] = conv_id
        await db.execute(
            "INSERT INTO chat_conversations (id, session_id, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (conv_id, sid, "", first, last),
        )

    await db.execute(_CHAT_MESSAGES.replace("chat_messages", "chat_messages_v2", 1))
    for sid, conv_id in conv_for.items():
        await db.execute(
            "INSERT INTO chat_messages_v2 (conversation_id, ord, role, content, created_at) "
            "SELECT ?, ord, role, content, created_at FROM chat_messages WHERE session_id = ?",
            (conv_id, sid),
        )
    await db.execute("DROP TABLE chat_messages")
    await db.execute("ALTER TABLE chat_messages_v2 RENAME TO chat_messages")


async def _migrate_1_pre_versioning(db: aiosqlite.Connection) -> None:
    """0 → 1: bring a file from before schema versioning up to date.

    Version-0 files were built by running the schema with IF NOT EXISTS on
    every open plus two probe-and-patch fixes (the chat rebuild, the group_id
    column), so one may be in any of several shapes. This step re-does exactly
    those idempotent moves, once. It is the last migration that has to probe:
    from here on ``user_version`` says what a file has.
    """
    for stmt in _BASE_TABLES:
        await db.execute(stmt)
    chat_cols = await _columns(db, "chat_messages")
    if not chat_cols:
        await db.execute(_CHAT_MESSAGES)
    elif "conversation_id" not in chat_cols:
        await _rebuild_legacy_chat(db)
    await db.execute(_CHAT_MESSAGES_INDEX)
    if "group_id" not in await _columns(db, "sessions"):
        await db.execute("ALTER TABLE sessions ADD COLUMN group_id TEXT")


async def _migrate_2_session_status(db: aiosqlite.Connection) -> None:
    """1 → 2: sessions get a lifecycle.

    ``status`` (see SESSION_STATUSES) lets the client show "still being
    transcribed" for a session whose recording is being re-run through
    Whisper as a whole file, ``status_detail`` carries the failure reason when
    that pass dies, and ``refined_at`` records that it happened. Every
    existing session is a finished one, so ``ready`` is the right default.
    """
    await db.execute("ALTER TABLE sessions ADD COLUMN status TEXT NOT NULL DEFAULT 'ready'")
    await db.execute("ALTER TABLE sessions ADD COLUMN status_detail TEXT")
    await db.execute("ALTER TABLE sessions ADD COLUMN refined_at TEXT")


async def _migrate_3_minutes(db: aiosqlite.Connection) -> None:
    """2 → 3: a table for the minutes the chat model writes (see core/minutes.py)."""
    await db.execute(_BASE_TABLES[-1])


# Ordered. Each step takes a file at the previous version to its own; the
# runner wraps it in a transaction and stamps the version on commit. Append,
# never edit or reorder: a step that already ran somewhere is history.
MIGRATIONS: tuple[tuple[int, Callable[[aiosqlite.Connection], Awaitable[None]]], ...] = (
    (1, _migrate_1_pre_versioning),
    (2, _migrate_2_session_status),
    (3, _migrate_3_minutes),
)


class Store:
    """Thin async DAL — one Store instance per FastAPI app."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    @property
    def path(self) -> Path:
        return self._db_path

    async def open(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA foreign_keys = ON")
        await self._db.execute("PRAGMA journal_mode = WAL")
        try:
            await self._upgrade(self._db)
        except BaseException:
            await self._db.close()
            self._db = None
            raise
        log.info("SQLite store opened at %s (schema v%d)", self._db_path, SCHEMA_VERSION)

    async def _upgrade(self, db: aiosqlite.Connection) -> None:
        """Create or migrate the file to ``SCHEMA_VERSION``."""
        version = await _user_version(db)
        if version > SCHEMA_VERSION:
            raise StoreVersionError(
                f"{self._db_path} is schema v{version}; this Wrenote knows up to "
                f"v{SCHEMA_VERSION}. It was written by a newer version — update Wrenote, "
                "or move the file aside to start empty."
            )
        if version == SCHEMA_VERSION:
            return
        if version == 0 and not await _columns(db, "sessions"):
            # A new file: build the current schema outright.
            await db.executescript(SCHEMA)
            await db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            await db.commit()
            return

        await self._backup(db, version)
        for target, step in MIGRATIONS:
            if target <= version:
                continue
            log.info("migrating %s: schema v%d → v%d", self._db_path, version, target)
            # BEGIN explicitly: sqlite3's implicit transactions don't cover DDL,
            # and the point is that a step either lands whole or not at all.
            await db.execute("BEGIN IMMEDIATE")
            try:
                await step(db)
                await db.execute(f"PRAGMA user_version = {target}")
                await db.execute("COMMIT")
            except BaseException:
                await db.execute("ROLLBACK")
                raise
            version = target

    async def _backup(self, db: aiosqlite.Connection, version: int) -> None:
        """Copy the file (via SQLite's backup API, so WAL content is included)
        before the first migration touches it. Overwrites a previous attempt's
        copy for the same source version; the user has the one that matters."""
        target = self._db_path.with_name(f"{self._db_path.name}.v{version}.bak")
        target.unlink(missing_ok=True)
        bak = await aiosqlite.connect(target)
        try:
            await db.backup(bak)
        finally:
            await bak.close()
        log.info("backed up %s to %s before migrating", self._db_path, target)

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @asynccontextmanager
    async def _conn(self) -> AsyncIterator[aiosqlite.Connection]:
        if self._db is None:
            raise RuntimeError("Store not opened")
        yield self._db

    # ---------- Sessions ----------

    async def upsert_session(
        self,
        *,
        session_id: str,
        title: str,
        created_at: str,
        src_lang: str,
        tgt_lang: str,
        duration_s: float = 0.0,
        status: str = "ready",
    ) -> None:
        if status not in SESSION_STATUSES:
            raise ValueError(f"unknown session status {status!r}")
        async with self._conn() as db:
            await db.execute(
                """
                INSERT INTO sessions (id, title, created_at, src_lang, tgt_lang, duration_s, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    src_lang = excluded.src_lang,
                    tgt_lang = excluded.tgt_lang,
                    duration_s = excluded.duration_s,
                    status = excluded.status,
                    status_detail = NULL
                """,
                (session_id, title, created_at, src_lang, tgt_lang, duration_s, status),
            )
            await db.commit()

    async def set_session_status(
        self, session_id: str, status: str, *, detail: str | None = None
    ) -> None:
        """Move a session along its lifecycle. ``detail`` is the reason when
        ``status`` is ``failed`` (and cleared otherwise)."""
        if status not in SESSION_STATUSES:
            raise ValueError(f"unknown session status {status!r}")
        async with self._conn() as db:
            await db.execute(
                "UPDATE sessions SET status = ?, status_detail = ? WHERE id = ?",
                (status, detail if status == "failed" else None, session_id),
            )
            await db.commit()

    async def mark_refined(self, session_id: str, refined_at: str) -> None:
        """The transcript now comes from a whole-recording pass."""
        async with self._conn() as db:
            await db.execute(
                "UPDATE sessions SET refined_at = ?, status = 'ready', status_detail = NULL "
                "WHERE id = ?",
                (refined_at, session_id),
            )
            await db.commit()

    async def recover_statuses(self) -> int:
        """Settle sessions a previous process left mid-flight.

        Run once at startup. A session still ``recording`` belongs to a
        connection that no longer exists — its rows are whatever got
        persisted before the crash, so it is ``ready``. A session still
        ``processing`` was in a job this process knows nothing about; the
        previous transcript is intact, so mark it ``failed`` with a reason the
        client can show and let the user re-run it.
        """
        async with self._conn() as db:
            cur = await db.execute(
                "UPDATE sessions SET status = 'ready' WHERE status = 'recording'"
            )
            n = cur.rowcount
            cur = await db.execute(
                "UPDATE sessions SET status = 'failed', status_detail = 'interrupted' "
                "WHERE status = 'processing'"
            )
            n += cur.rowcount
            await db.commit()
        return n

    async def update_session_title(self, session_id: str, title: str) -> None:
        async with self._conn() as db:
            await db.execute(
                "UPDATE sessions SET title = ? WHERE id = ?",
                (title, session_id),
            )
            await db.commit()

    async def update_session_duration(self, session_id: str, duration_s: float) -> None:
        async with self._conn() as db:
            await db.execute(
                "UPDATE sessions SET duration_s = ? WHERE id = ?",
                (duration_s, session_id),
            )
            await db.commit()

    async def list_sessions(self) -> list[dict[str, Any]]:
        async with self._conn() as db:
            cur = await db.execute(
                f"SELECT {_SESSION_COLUMNS} FROM sessions ORDER BY created_at DESC"
            )
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ---------- Session groups ----------

    async def list_groups(self) -> list[dict[str, Any]]:
        async with self._conn() as db:
            cur = await db.execute(
                "SELECT id, name, created_at, position FROM session_groups "
                "ORDER BY position ASC, created_at ASC"
            )
            return [dict(r) for r in await cur.fetchall()]

    async def create_group(
        self, *, group_id: str, name: str, created_at: str, position: int
    ) -> None:
        async with self._conn() as db:
            await db.execute(
                "INSERT INTO session_groups (id, name, created_at, position) "
                "VALUES (?, ?, ?, ?)",
                (group_id, name, created_at, position),
            )
            await db.commit()

    async def rename_group(self, group_id: str, name: str) -> None:
        async with self._conn() as db:
            await db.execute(
                "UPDATE session_groups SET name = ? WHERE id = ?",
                (name, group_id),
            )
            await db.commit()

    async def delete_group(self, group_id: str) -> bool:
        async with self._conn() as db:
            await db.execute(
                "UPDATE sessions SET group_id = NULL WHERE group_id = ?",
                (group_id,),
            )
            cur = await db.execute(
                "DELETE FROM session_groups WHERE id = ?",
                (group_id,),
            )
            await db.commit()
            return cur.rowcount > 0

    async def set_session_group(
        self, session_id: str, group_id: str | None
    ) -> None:
        async with self._conn() as db:
            await db.execute(
                "UPDATE sessions SET group_id = ? WHERE id = ?",
                (group_id, session_id),
            )
            await db.commit()

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        async with self._conn() as db:
            cur = await db.execute(
                f"SELECT {_SESSION_COLUMNS} FROM sessions WHERE id = ?",
                (session_id,),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            session = dict(row)
            cur = await db.execute(
                """
                SELECT segment_id, ord, started_at, ended_at,
                       orig_text, orig_status, orig_lang,
                       trans_text, trans_status, trans_lang, speaker
                FROM segments WHERE session_id = ? ORDER BY ord ASC
                """,
                (session_id,),
            )
            segs = [dict(r) for r in await cur.fetchall()]
        session["segments"] = segs
        return session

    async def delete_session(self, session_id: str) -> bool:
        async with self._conn() as db:
            cur = await db.execute(
                "DELETE FROM sessions WHERE id = ?",
                (session_id,),
            )
            await db.commit()
            return cur.rowcount > 0

    # ---------- Segments ----------

    # ---------- Chat conversations ----------

    async def list_conversations(self, session_id: str) -> list[dict[str, Any]]:
        """All threads for a session, most-recently-active first, each with a
        message count so the UI can dim empty ones."""
        async with self._conn() as db:
            cur = await db.execute(
                """
                SELECT c.id, c.title, c.created_at, c.updated_at,
                       (SELECT COUNT(*) FROM chat_messages m
                        WHERE m.conversation_id = c.id) AS message_count
                FROM chat_conversations c
                WHERE c.session_id = ?
                ORDER BY c.updated_at DESC
                """,
                (session_id,),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        async with self._conn() as db:
            cur = await db.execute(
                "SELECT id, session_id, title, created_at, updated_at "
                "FROM chat_conversations WHERE id = ?",
                (conversation_id,),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def create_conversation(
        self,
        *,
        conversation_id: str,
        session_id: str,
        title: str,
        created_at: str,
    ) -> None:
        async with self._conn() as db:
            await db.execute(
                "INSERT INTO chat_conversations (id, session_id, title, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (conversation_id, session_id, title, created_at, created_at),
            )
            await db.commit()

    async def rename_conversation(self, conversation_id: str, title: str) -> None:
        async with self._conn() as db:
            await db.execute(
                "UPDATE chat_conversations SET title = ? WHERE id = ?",
                (title, conversation_id),
            )
            await db.commit()

    async def touch_conversation(self, conversation_id: str, updated_at: str) -> None:
        """Bump updated_at so the thread floats to the top of the list."""
        async with self._conn() as db:
            await db.execute(
                "UPDATE chat_conversations SET updated_at = ? WHERE id = ?",
                (updated_at, conversation_id),
            )
            await db.commit()

    async def delete_conversation(self, conversation_id: str) -> bool:
        async with self._conn() as db:
            cur = await db.execute(
                "DELETE FROM chat_conversations WHERE id = ?",
                (conversation_id,),
            )
            await db.commit()
            return cur.rowcount > 0

    # ---------- Chat messages (scoped to a conversation) ----------

    async def list_chat_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        async with self._conn() as db:
            cur = await db.execute(
                "SELECT ord, role, content, created_at "
                "FROM chat_messages WHERE conversation_id = ? ORDER BY ord ASC",
                (conversation_id,),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def append_chat_message(
        self,
        *,
        conversation_id: str,
        role: str,
        content: str,
        created_at: str,
    ) -> int:
        """Append at the next ordinal within the conversation; returns the ord."""
        async with self._conn() as db:
            cur = await db.execute(
                "SELECT COALESCE(MAX(ord), -1) + 1 AS next_ord "
                "FROM chat_messages WHERE conversation_id = ?",
                (conversation_id,),
            )
            row = await cur.fetchone()
            next_ord = int(row["next_ord"]) if row else 0
            await db.execute(
                "INSERT INTO chat_messages (conversation_id, ord, role, content, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (conversation_id, next_ord, role, content, created_at),
            )
            await db.commit()
            return next_ord

    async def clear_chat(self, conversation_id: str) -> None:
        async with self._conn() as db:
            await db.execute(
                "DELETE FROM chat_messages WHERE conversation_id = ?",
                (conversation_id,),
            )
            await db.commit()

    # ---------- Speakers ----------

    async def set_segment_speakers(
        self, session_id: str, labels: dict[str, str]
    ) -> int:
        """Apply a {segment_id: label} mapping in one transaction.
        Returns number of rows touched."""
        if not labels:
            return 0
        async with self._conn() as db:
            await db.executemany(
                "UPDATE segments SET speaker = ? "
                "WHERE session_id = ? AND segment_id = ?",
                [(label, session_id, sid) for sid, label in labels.items()],
            )
            await db.commit()
            return len(labels)

    async def rename_speaker(
        self, session_id: str, old_label: str, new_label: str
    ) -> int:
        """Rename every segment whose speaker = old_label. Returns count."""
        async with self._conn() as db:
            cur = await db.execute(
                "UPDATE segments SET speaker = ? "
                "WHERE session_id = ? AND speaker = ?",
                (new_label, session_id, old_label),
            )
            await db.commit()
            return cur.rowcount

    async def update_segment_text(
        self,
        session_id: str,
        segment_id: str,
        *,
        orig_text: str | None = None,
        trans_text: str | None = None,
    ) -> bool:
        """Edit a segment's text. Editing the original marks any existing
        translation ``stale`` (so the Translate action refreshes it); editing
        the translation directly is a manual override (status ``final``).
        Returns False if no such segment exists."""
        sets: list[str] = []
        params: list[Any] = []
        if orig_text is not None:
            sets.append("orig_text = ?")
            params.append(orig_text)
        if trans_text is not None:
            sets.append("trans_text = ?")
            params.append(trans_text)
            sets.append("trans_status = 'final'")
        elif orig_text is not None:
            # Original changed → its translation no longer matches.
            sets.append(
                "trans_status = CASE WHEN trans_text != '' THEN 'stale' ELSE trans_status END"
            )
        if not sets:
            return False
        params += [session_id, segment_id]
        async with self._conn() as db:
            # NB: the SET clause is assembled from a fixed allowlist of columns
            # above (never user input); values are bound parameters.
            cur = await db.execute(
                f"UPDATE segments SET {', '.join(sets)} "
                "WHERE session_id = ? AND segment_id = ?",
                params,
            )
            await db.commit()
            return cur.rowcount > 0

    # ---------- Minutes ----------

    async def list_minutes(self, session_id: str) -> list[dict[str, Any]]:
        async with self._conn() as db:
            cur = await db.execute(
                "SELECT session_id, lang, content, generated_at, model, transcript_hash "
                "FROM session_minutes WHERE session_id = ? ORDER BY generated_at DESC",
                (session_id,),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def upsert_minutes(
        self,
        *,
        session_id: str,
        lang: str,
        content: str,
        generated_at: str,
        model: str,
        transcript_hash: str,
    ) -> None:
        async with self._conn() as db:
            await db.execute(
                """
                INSERT INTO session_minutes
                    (session_id, lang, content, generated_at, model, transcript_hash)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, lang) DO UPDATE SET
                    content = excluded.content,
                    generated_at = excluded.generated_at,
                    model = excluded.model,
                    transcript_hash = excluded.transcript_hash
                """,
                (session_id, lang, content, generated_at, model, transcript_hash),
            )
            await db.commit()

    async def delete_minutes(self, session_id: str, lang: str) -> bool:
        async with self._conn() as db:
            cur = await db.execute(
                "DELETE FROM session_minutes WHERE session_id = ? AND lang = ?",
                (session_id, lang),
            )
            await db.commit()
            return cur.rowcount > 0

    # ---------- Glossary (custom vocabulary) ----------

    async def list_glossary(self) -> list[dict[str, Any]]:
        async with self._conn() as db:
            cur = await db.execute(
                "SELECT id, term, translation, note, position "
                "FROM glossary ORDER BY position, term"
            )
            return [dict(r) for r in await cur.fetchall()]

    async def replace_glossary(self, entries: list[dict[str, Any]]) -> int:
        """Replace the whole glossary in one transaction. Each entry needs a
        ``term``; ``translation`` / ``note`` are optional."""
        async with self._conn() as db:
            await db.execute("DELETE FROM glossary")
            await db.executemany(
                "INSERT INTO glossary (id, term, translation, note, position) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        str(e.get("id") or uuid.uuid4().hex),
                        str(e["term"]).strip(),
                        str(e.get("translation") or "").strip(),
                        str(e.get("note") or "").strip(),
                        i,
                    )
                    for i, e in enumerate(entries)
                    if str(e.get("term") or "").strip()
                ],
            )
            await db.commit()
        return len(entries)

    # ---------- Segments ----------

    async def replace_segments(
        self,
        session_id: str,
        rows: list[dict[str, Any]],
    ) -> int:
        """Replace all transcript rows for a session in one transaction.

        Used by offline speaker-aware resegmentation. The recording file and
        session metadata stay untouched; translations are expected to be
        regenerated because segment boundaries/text changed.
        """
        async with self._conn() as db:
            await db.execute("DELETE FROM segments WHERE session_id = ?", (session_id,))
            await db.executemany(
                """
                INSERT INTO segments (
                    session_id, segment_id, ord, started_at, ended_at,
                    orig_text, orig_status, orig_lang,
                    trans_text, trans_status, trans_lang, speaker
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        session_id,
                        row["segment_id"],
                        row["ord"],
                        row["started_at"],
                        row["ended_at"],
                        row["orig_text"],
                        row.get("orig_status", "final"),
                        row.get("orig_lang"),
                        row.get("trans_text", ""),
                        row.get("trans_status", "skipped"),
                        row.get("trans_lang"),
                        row.get("speaker"),
                    )
                    for row in rows
                ],
            )
            await db.commit()
            return len(rows)

    async def upsert_segment_orig(
        self,
        *,
        session_id: str,
        segment_id: str,
        ord_: int,
        started_at: float,
        ended_at: float,
        orig_text: str,
        orig_status: str,
        orig_lang: str | None = None,
        speaker: str | None = None,
    ) -> None:
        """Insert or update only the original-transcript half of a segment.

        Translation fields on an existing row are preserved as-is. Speaker /
        orig_lang use COALESCE so a later partial without those fields
        doesn't clobber an earlier final that had them.
        """
        async with self._conn() as db:
            await db.execute(
                """
                INSERT INTO segments (
                    session_id, segment_id, ord, started_at, ended_at,
                    orig_text, orig_status, orig_lang, speaker
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, segment_id) DO UPDATE SET
                    ord = excluded.ord,
                    started_at = excluded.started_at,
                    ended_at = excluded.ended_at,
                    orig_text = excluded.orig_text,
                    orig_status = excluded.orig_status,
                    orig_lang = COALESCE(excluded.orig_lang, segments.orig_lang),
                    speaker = COALESCE(excluded.speaker, segments.speaker)
                """,
                (
                    session_id, segment_id, ord_, started_at, ended_at,
                    orig_text, orig_status, orig_lang, speaker,
                ),
            )
            await db.commit()

    async def upsert_segment_trans(
        self,
        *,
        session_id: str,
        segment_id: str,
        ord_: int,
        trans_text: str,
        trans_status: str,
        trans_lang: str | None = None,
        speaker: str | None = None,
    ) -> None:
        """Insert or update only the translation half of a segment.

        If the row doesn't exist yet (rare — translation usually arrives
        after the transcript), inserts with empty orig fields; a later
        transcript upsert fills those in. Original-transcript fields on an
        existing row are NOT touched.
        """
        async with self._conn() as db:
            await db.execute(
                """
                INSERT INTO segments (
                    session_id, segment_id, ord, started_at, ended_at,
                    orig_text, orig_status, orig_lang,
                    trans_text, trans_status, trans_lang, speaker
                ) VALUES (?, ?, ?, ?, ?, '', 'final', NULL, ?, ?, ?, ?)
                ON CONFLICT(session_id, segment_id) DO UPDATE SET
                    trans_text = excluded.trans_text,
                    trans_status = excluded.trans_status,
                    trans_lang = COALESCE(excluded.trans_lang, segments.trans_lang),
                    speaker = COALESCE(excluded.speaker, segments.speaker)
                """,
                (
                    session_id, segment_id, ord_, 0.0, 0.0,
                    trans_text, trans_status, trans_lang, speaker,
                ),
            )
            await db.commit()
