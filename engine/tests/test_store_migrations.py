"""Schema versioning in core/store.py.

The user's whole library is one SQLite file, and it is the only copy. These
tests hold the rules that keep an upgrade from eating it: a new file gets the
current schema, an old file is migrated with its rows intact, a migration that
dies lands nothing, a file from a newer Wrenote is refused rather than guessed
at, and a copy exists before anything is touched.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import wrenote.core.store as store_mod
from wrenote.core.store import SCHEMA_VERSION, Store, StoreVersionError

# The oldest shape a data.db ever had: no groups, no glossary, and chat
# messages hanging off the session instead of a conversation.
LEGACY_SCHEMA = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL,
    src_lang TEXT NOT NULL, tgt_lang TEXT NOT NULL, duration_s REAL NOT NULL DEFAULT 0
);
CREATE TABLE segments (
    session_id TEXT NOT NULL, segment_id TEXT NOT NULL, ord INTEGER NOT NULL,
    started_at REAL NOT NULL, ended_at REAL NOT NULL,
    orig_text TEXT NOT NULL DEFAULT '', orig_status TEXT NOT NULL DEFAULT 'final', orig_lang TEXT,
    trans_text TEXT NOT NULL DEFAULT '', trans_status TEXT NOT NULL DEFAULT 'final', trans_lang TEXT,
    speaker TEXT,
    PRIMARY KEY (session_id, segment_id),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE TABLE chat_messages (
    session_id TEXT NOT NULL, ord INTEGER NOT NULL, role TEXT NOT NULL,
    content TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY (session_id, ord)
);
INSERT INTO sessions VALUES ('s1', 'Standup', '2026-01-01T09:00:00', 'en', 'zh', 120.0);
INSERT INTO segments (session_id, segment_id, ord, started_at, ended_at, orig_text)
    VALUES ('s1', 'a', 0, 0.0, 2.0, 'hello');
INSERT INTO chat_messages VALUES ('s1', 0, 'user', 'summarize', '2026-01-01T10:00:00');
INSERT INTO chat_messages VALUES ('s1', 1, 'assistant', 'A standup.', '2026-01-01T10:00:05');
"""


def _legacy_file(path: Path) -> Path:
    with sqlite3.connect(path) as c:
        c.executescript(LEGACY_SCHEMA)
    return path


def _version(path: Path) -> int:
    with sqlite3.connect(path) as c:
        return int(c.execute("PRAGMA user_version").fetchone()[0])


def _shape(path: Path) -> dict[str, object]:
    """Tables with their columns (name, type, notnull, default, pk) plus index names —
    everything that matters about a schema, minus the SQL text and comments."""
    with sqlite3.connect(path) as c:
        tables = [
            r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        return {
            t: {
                "columns": [tuple(r[1:]) for r in c.execute(f"PRAGMA table_info({t})")],
                "indexes": sorted(r[1] for r in c.execute(f"PRAGMA index_list({t})")),
            }
            for t in sorted(tables)
        }


def _backups(path: Path) -> list[Path]:
    return sorted(path.parent.glob(f"{path.name}.v*.bak"))


async def test_new_file_is_created_at_the_current_version(tmp_path):
    s = Store(tmp_path / "data.db")
    await s.open()
    await s.close()
    assert _version(s.path) == SCHEMA_VERSION
    assert set(_shape(s.path)) == {
        "sessions", "session_groups", "segments", "chat_conversations",
        "chat_messages", "glossary",
    }
    # Nothing to protect yet, so nothing to copy.
    assert _backups(s.path) == []


async def test_legacy_file_is_migrated_with_its_rows(tmp_path):
    path = _legacy_file(tmp_path / "data.db")
    s = Store(path)
    await s.open()
    try:
        sess = await s.get_session("s1")
        assert sess is not None and sess["title"] == "Standup"
        assert [seg["orig_text"] for seg in sess["segments"]] == ["hello"]
        # The session's chat history survives, wrapped in one conversation.
        convs = await s.list_conversations("s1")
        assert len(convs) == 1
        msgs = await s.list_chat_messages(convs[0]["id"])
        assert [(m["role"], m["content"]) for m in msgs] == [
            ("user", "summarize"), ("assistant", "A standup."),
        ]
        # And the newer tables exist: this used to be "created on every open".
        assert await s.list_groups() == []
        assert await s.list_glossary() == []
    finally:
        await s.close()
    assert _version(path) == SCHEMA_VERSION


async def test_migrated_file_has_the_same_shape_as_a_new_one(tmp_path):
    """The check that keeps SCHEMA and MIGRATIONS honest with each other: a
    migration that forgets a column, or a SCHEMA edit without a migration,
    shows up here as two files that disagree."""
    fresh = Store(tmp_path / "fresh.db")
    await fresh.open()
    await fresh.close()
    migrated = Store(_legacy_file(tmp_path / "legacy.db"))
    await migrated.open()
    await migrated.close()
    assert _shape(migrated.path) == _shape(fresh.path)


async def test_pre_versioning_file_that_was_already_patched(tmp_path):
    """A file written by the last release has every table (the old code created
    them on each open) but no user_version. It must be stamped, not rebuilt:
    no duplicate conversations, no lost rows."""
    s = Store(tmp_path / "data.db")
    await s.open()
    await s.upsert_session(
        session_id="s1", title="T", created_at="2026-01-01T00:00:00", src_lang="en", tgt_lang="zh",
    )
    await s.create_conversation(
        conversation_id="c1", session_id="s1", title="first", created_at="2026-01-01T00:00:00",
    )
    await s.append_chat_message(
        conversation_id="c1", role="user", content="hi", created_at="2026-01-01T00:00:01",
    )
    await s.close()
    with sqlite3.connect(s.path) as c:
        c.execute("PRAGMA user_version = 0")

    s = Store(s.path)
    await s.open()
    try:
        convs = await s.list_conversations("s1")
        assert [cv["title"] for cv in convs] == ["first"]
        assert [m["content"] for m in await s.list_chat_messages("c1")] == ["hi"]
    finally:
        await s.close()
    assert _version(s.path) == SCHEMA_VERSION


async def test_a_copy_is_taken_before_migrating(tmp_path):
    path = _legacy_file(tmp_path / "data.db")
    s = Store(path)
    await s.open()
    await s.close()
    (bak,) = _backups(path)
    assert bak.name == "data.db.v0.bak"
    # It is the file as it was, not the migrated one.
    assert _version(bak) == 0
    assert "session_id" in {c[0] for c in _shape(bak)["chat_messages"]["columns"]}  # type: ignore[index]


async def test_reopening_a_current_file_touches_nothing(tmp_path):
    s = Store(tmp_path / "data.db")
    await s.open()
    await s.close()
    await s.open()
    await s.close()
    assert _backups(s.path) == []
    assert _version(s.path) == SCHEMA_VERSION


async def test_a_newer_file_is_refused(tmp_path):
    s = Store(tmp_path / "data.db")
    await s.open()
    await s.close()
    with sqlite3.connect(s.path) as c:
        c.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 5}")

    with pytest.raises(StoreVersionError):
        await Store(s.path).open()
    # Untouched, so the newer Wrenote can still open it.
    assert _version(s.path) == SCHEMA_VERSION + 5
    assert _backups(s.path) == []


async def test_a_failing_migration_lands_nothing(tmp_path, monkeypatch):
    """One transaction per step: a step that dies after creating a table and
    stamping its version leaves neither behind, and the file still opens at
    the old version with the old code."""
    s = Store(tmp_path / "data.db")
    await s.open()
    await s.close()

    async def explode(db):
        await db.execute("CREATE TABLE half_done (x INTEGER)")
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(store_mod, "SCHEMA_VERSION", SCHEMA_VERSION + 1)
    monkeypatch.setattr(store_mod, "MIGRATIONS", (*store_mod.MIGRATIONS, (SCHEMA_VERSION + 1, explode)))
    broken = Store(s.path)
    with pytest.raises(RuntimeError, match="disk on fire"):
        await broken.open()
    assert _version(s.path) == SCHEMA_VERSION
    assert "half_done" not in _shape(s.path)
    monkeypatch.undo()

    s = Store(s.path)
    await s.open()
    await s.close()
    assert _version(s.path) == SCHEMA_VERSION


async def test_migrations_are_dense_and_end_at_schema_version():
    versions = [v for v, _ in store_mod.MIGRATIONS]
    assert versions == list(range(1, SCHEMA_VERSION + 1))
