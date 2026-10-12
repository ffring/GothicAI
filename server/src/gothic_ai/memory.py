"""What npc's remember, per save game: an append-only log in SQLite, branched like the saves themselves.

A *branch* is one line of play. The game starts a new one on "new game" and on every load; a load
branches off the saved branch at the sequence number written into the save, so whatever happened
after that save is forgotten, exactly like in the game world. Each branch holds its own copy of the
events it inherited, which keeps reads to one branch.

Events per npc: dialogue messages (`user` = the hero, `assistant` = the npc, `developer` = notes)
and summaries that fold older messages into a short recollection. Events of the branch as a whole:
patches of the story graph (story.py), which follow the saves the same way.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from .llm import Message, Role

log = logging.getLogger(__name__)

DEFAULT_PATH = Path(os.environ.get("GOTHIC_AI_DATA", Path.home() / ".local" / "share" / "gothic-ai")) / (
    "memory.sqlite3"
)
LOAD_LIMIT = 100  # newest messages read back for one npc (older ones should be summarized already)
PATCHES = "*"  # the "npc" of story graph patches: they belong to the branch

_SCHEMA = """
CREATE TABLE IF NOT EXISTS branches (
    id         TEXT PRIMARY KEY,
    parent     TEXT,
    parent_seq INTEGER NOT NULL DEFAULT 0,
    created    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    branch    TEXT NOT NULL,
    seq       INTEGER NOT NULL,
    npc       TEXT NOT NULL,
    kind      TEXT NOT NULL,          -- message | summary | patch (npc '*', content: JSON of the operation)
    role      TEXT,                   -- message: user | assistant | developer
    content   TEXT NOT NULL,
    covers    INTEGER,                -- summary: messages up to this seq are folded in
    game_time TEXT,
    created   REAL NOT NULL,
    PRIMARY KEY (branch, seq)
);
CREATE INDEX IF NOT EXISTS events_by_npc ON events (branch, npc, seq);
"""


@dataclass(frozen=True)
class Entry:
    seq: int
    role: Role
    content: str

    def message(self) -> Message:
        return Message(self.role, self.content)


@dataclass
class NpcMemory:
    summary: str = ""
    summary_seq: int = 0  # entries up to this seq are folded into the summary
    entries: list[Entry] = field(default_factory=list)


@dataclass(frozen=True)
class BranchInfo:
    id: str
    parent: str | None
    parent_seq: int
    created: float
    head: int
    npcs: int


class MemoryStore:
    def __init__(self, path: str | Path = DEFAULT_PATH) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._db = sqlite3.connect(str(path), isolation_level=None)  # autocommit; explicit transactions
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def ensure_branch(self, branch: str, parent: str | None = None, parent_seq: int = 0) -> int:
        """Create the branch on first sight (copying what it inherits from `parent`). Returns its head."""
        if self._db.execute("SELECT 1 FROM branches WHERE id=?", (branch,)).fetchone():
            return self.head(branch)
        with self._tx():
            self._db.execute(
                "INSERT INTO branches (id, parent, parent_seq, created) VALUES (?,?,?,?)",
                (branch, parent, parent_seq, time.time()),
            )
            if parent:
                copied = self._db.execute(
                    "INSERT INTO events SELECT ?, seq, npc, kind, role, content, covers, game_time, created "
                    "FROM events WHERE branch=? AND seq<=?",
                    (branch, parent, parent_seq),
                ).rowcount
                if not copied and parent_seq and not self._known(parent):
                    log.warning("branch %s: parent %s is unknown, starting without memories", branch, parent)
        return self.head(branch)

    def head(self, branch: str) -> int:
        row = self._db.execute("SELECT MAX(seq) FROM events WHERE branch=?", (branch,)).fetchone()
        return int(row[0] or 0)

    def append(self, branch: str, npc: str, role: Role, content: str, game_time: str | None = None) -> int:
        return self._insert(branch, npc, "message", role, content, None, game_time)

    def add_summary(self, branch: str, npc: str, summary: str, covers: int) -> int:
        return self._insert(branch, npc, "summary", None, summary, covers, None)

    def load(self, branch: str, npc: str) -> NpcMemory:
        mem = NpcMemory()
        row = self._db.execute(
            "SELECT content, covers FROM events WHERE branch=? AND npc=? AND kind='summary' "
            "ORDER BY seq DESC LIMIT 1",
            (branch, npc),
        ).fetchone()
        if row:
            mem.summary, mem.summary_seq = row[0], int(row[1] or 0)
        rows = self._db.execute(
            "SELECT seq, role, content FROM events WHERE branch=? AND npc=? AND kind='message' AND seq>? "
            "ORDER BY seq DESC LIMIT ?",
            (branch, npc, mem.summary_seq, LOAD_LIMIT),
        ).fetchall()
        mem.entries = [Entry(int(seq), role, content) for seq, role, content in reversed(rows)]
        return mem

    def add_patch(self, branch: str, op: str) -> int:
        """A change to the story graph (JSON of one operation) made on this branch."""
        return self._insert(branch, PATCHES, "patch", None, op, None, None)

    def patches(self, branch: str) -> list[str]:
        """The story graph changes of the branch, oldest first (inherited ones included)."""
        rows = self._db.execute(
            "SELECT content FROM events WHERE branch=? AND kind='patch' ORDER BY seq", (branch,)
        ).fetchall()
        return [row[0] for row in rows]

    def branches(self) -> list[BranchInfo]:
        rows = self._db.execute(
            "SELECT b.id, b.parent, b.parent_seq, b.created, COALESCE(MAX(e.seq), 0), "
            "COUNT(DISTINCT CASE WHEN e.kind != 'patch' THEN e.npc END) "
            "FROM branches b LEFT JOIN events e ON e.branch=b.id GROUP BY b.id ORDER BY b.created"
        ).fetchall()
        return [BranchInfo(*row) for row in rows]

    def _insert(
        self,
        branch: str,
        npc: str,
        kind: str,
        role: str | None,
        content: str,
        covers: int | None,
        game_time: str | None,
    ) -> int:
        with self._tx():
            seq = self.head(branch) + 1
            self._db.execute(
                "INSERT INTO events (branch, seq, npc, kind, role, content, covers, game_time, created) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (branch, seq, npc, kind, role, content, covers, game_time, time.time()),
            )
        return seq

    def _known(self, branch: str) -> bool:
        return (
            self._db.execute("SELECT 1 FROM events WHERE branch=? LIMIT 1", (branch,)).fetchone() is not None
        )

    def _tx(self) -> _Transaction:
        return _Transaction(self._db)


class _Transaction:
    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def __enter__(self) -> None:
        self._db.execute("BEGIN IMMEDIATE")

    def __exit__(self, exc_type, *_exc) -> None:
        self._db.execute("ROLLBACK" if exc_type else "COMMIT")
