"""SQLite-backed persistence for sessions, segments, and (later) chat.

Single-file DB at ``~/.wrenote/data.db``. Async access via aiosqlite —
the WS handler upserts as transcripts/translations arrive so a crash mid-
session still leaves the partial work persisted. Cascade-delete is wired
on the foreign key, but we also explicitly remove the per-session WAV file
(filesystem) from the same code path so the two stay in sync.

The frontend reads via the HTTP endpoints in :mod:`wrenote.server`;
LocalStorage is no longer authoritative.
"""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import aiosqlite

log = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path("~/.wrenote/data.db").expanduser()

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    src_lang     TEXT NOT NULL,
    tgt_lang     TEXT NOT NULL,
    duration_s   REAL NOT NULL DEFAULT 0,
    group_id     TEXT
);

-- Optional folders the sidebar groups sessions into. Membership is the
-- nullable sessions.group_id above; deleting a group just orphans its members.
CREATE TABLE IF NOT EXISTS session_groups (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    position    INTEGER NOT NULL DEFAULT 0
);

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
);

CREATE INDEX IF NOT EXISTS idx_segments_session_ord
    ON segments(session_id, ord);
CREATE INDEX IF NOT EXISTS idx_sessions_created_at
    ON sessions(created_at DESC);

-- A session can hold many chat threads ("conversations"). Messages hang off
-- a conversation, not the session directly. (chat_messages is created/migrated
-- separately in _migrate_chat so an older session-keyed table can be upgraded.)
CREATE TABLE IF NOT EXISTS chat_conversations (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_conversations_session
    ON chat_conversations(session_id, updated_at DESC);
"""

# chat_messages is managed by _migrate_chat() rather than the main SCHEMA: a DB
# created before multi-conversation has a session_id-keyed table we must rebuild
# in place, and CREATE INDEX on conversation_id would fail against that old shape.
CHAT_MESSAGES_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_messages (
    conversation_id TEXT NOT NULL,
    ord             INTEGER NOT NULL,
    role            TEXT NOT NULL,                  -- 'user' | 'assistant'
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (conversation_id, ord),
    FOREIGN KEY (conversation_id) REFERENCES chat_conversations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_chat_conversation_ord
    ON chat_messages(conversation_id, ord);
"""


class Store:
    """Thin async DAL — one Store instance per FastAPI app."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or DEFAULT_DB_PATH
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA foreign_keys = ON")
        await self._db.execute("PRAGMA journal_mode = WAL")
        await self._db.executescript(SCHEMA)
        await self._migrate_chat(self._db)
        await self._migrate_groups(self._db)
        await self._db.commit()
        log.info("SQLite store opened at %s", self._db_path)

    async def _migrate_groups(self, db: aiosqlite.Connection) -> None:
        """Add sessions.group_id to a DB created before grouping existed."""
        cur = await db.execute("PRAGMA table_info(sessions)")
        cols = {row["name"] for row in await cur.fetchall()}
        if "group_id" not in cols:
            await db.execute("ALTER TABLE sessions ADD COLUMN group_id TEXT")

    async def _migrate_chat(self, db: aiosqlite.Connection) -> None:
        """Create chat_messages, upgrading a legacy session-keyed table.

        Old DBs keyed messages by ``session_id``; the conversation model keys
        them by ``conversation_id``. We rebuild the table once, wrapping each
        session's existing messages in a single migrated conversation so no
        chat history is lost.
        """
        cur = await db.execute("PRAGMA table_info(chat_messages)")
        cols = {row["name"] for row in await cur.fetchall()}

        if not cols:
            # Fresh DB — just create the current schema.
            await db.executescript(CHAT_MESSAGES_SCHEMA)
            return
        if "conversation_id" in cols:
            # Already migrated; make sure the index is present.
            await db.executescript(CHAT_MESSAGES_SCHEMA)
            return

        # Legacy session-keyed table → rebuild.
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

        await db.execute(
            """
            CREATE TABLE chat_messages_v2 (
                conversation_id TEXT NOT NULL,
                ord             INTEGER NOT NULL,
                role            TEXT NOT NULL,
                content         TEXT NOT NULL,
                created_at      TEXT NOT NULL,
                PRIMARY KEY (conversation_id, ord),
                FOREIGN KEY (conversation_id) REFERENCES chat_conversations(id) ON DELETE CASCADE
            )
            """
        )
        for sid, conv_id in conv_for.items():
            await db.execute(
                "INSERT INTO chat_messages_v2 (conversation_id, ord, role, content, created_at) "
                "SELECT ?, ord, role, content, created_at FROM chat_messages WHERE session_id = ?",
                (conv_id, sid),
            )
        await db.execute("DROP TABLE chat_messages")
        await db.execute("ALTER TABLE chat_messages_v2 RENAME TO chat_messages")
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_conversation_ord "
            "ON chat_messages(conversation_id, ord)"
        )

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
    ) -> None:
        async with self._conn() as db:
            await db.execute(
                """
                INSERT INTO sessions (id, title, created_at, src_lang, tgt_lang, duration_s)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    src_lang = excluded.src_lang,
                    tgt_lang = excluded.tgt_lang,
                    duration_s = excluded.duration_s
                """,
                (session_id, title, created_at, src_lang, tgt_lang, duration_s),
            )
            await db.commit()

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
                "SELECT id, title, created_at, src_lang, tgt_lang, duration_s, group_id "
                "FROM sessions ORDER BY created_at DESC"
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
                "SELECT id, title, created_at, src_lang, tgt_lang, duration_s, group_id "
                "FROM sessions WHERE id = ?",
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
