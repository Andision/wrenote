"""SQLite-backed persistence for sessions, segments, and (later) chat.

Single-file DB at ``~/.interpreter/data.db``. Async access via aiosqlite —
the WS handler upserts as transcripts/translations arrive so a crash mid-
session still leaves the partial work persisted. Cascade-delete is wired
on the foreign key, but we also explicitly remove the per-session WAV file
(filesystem) from the same code path so the two stay in sync.

The frontend reads via the HTTP endpoints in :mod:`interpreter.server`;
LocalStorage is no longer authoritative.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import aiosqlite

log = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path("~/.interpreter/data.db").expanduser()

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    src_lang     TEXT NOT NULL,
    tgt_lang     TEXT NOT NULL,
    duration_s   REAL NOT NULL DEFAULT 0
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

CREATE TABLE IF NOT EXISTS chat_messages (
    session_id TEXT NOT NULL,
    ord        INTEGER NOT NULL,
    role       TEXT NOT NULL,                       -- 'user' | 'assistant'
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (session_id, ord),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_chat_session_ord
    ON chat_messages(session_id, ord);
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
        await self._db.commit()
        log.info("SQLite store opened at %s", self._db_path)

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
                "SELECT id, title, created_at, src_lang, tgt_lang, duration_s "
                "FROM sessions ORDER BY created_at DESC"
            )
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        async with self._conn() as db:
            cur = await db.execute(
                "SELECT id, title, created_at, src_lang, tgt_lang, duration_s "
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

    # ---------- Chat ----------

    async def list_chat_messages(self, session_id: str) -> list[dict[str, Any]]:
        async with self._conn() as db:
            cur = await db.execute(
                "SELECT ord, role, content, created_at "
                "FROM chat_messages WHERE session_id = ? ORDER BY ord ASC",
                (session_id,),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def append_chat_message(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        created_at: str,
    ) -> int:
        """Append at the next ordinal; returns the assigned ord."""
        async with self._conn() as db:
            cur = await db.execute(
                "SELECT COALESCE(MAX(ord), -1) + 1 AS next_ord "
                "FROM chat_messages WHERE session_id = ?",
                (session_id,),
            )
            row = await cur.fetchone()
            next_ord = int(row["next_ord"]) if row else 0
            await db.execute(
                "INSERT INTO chat_messages (session_id, ord, role, content, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, next_ord, role, content, created_at),
            )
            await db.commit()
            return next_ord

    async def clear_chat(self, session_id: str) -> None:
        async with self._conn() as db:
            await db.execute(
                "DELETE FROM chat_messages WHERE session_id = ?",
                (session_id,),
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
