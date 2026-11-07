"""Conversation memory: where LangGraph saves each chat's state between messages.

- sqlite (default): saved to data/checkpoints.db, survives restarts.
- memory: kept in RAM, wiped on restart (handy for quick experiments).
"""
import sqlite3
from contextlib import contextmanager
from typing import Iterator

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver

from app.config import get_settings

BACKENDS = ("sqlite", "memory")


@contextmanager
def open_checkpointer() -> Iterator[BaseCheckpointSaver]:
    """Open the configured checkpointer for the lifetime of the app, then close it."""
    settings = get_settings()
    backend = settings.memory_backend

    if backend == "memory":
        yield InMemorySaver()
        return
    if backend != "sqlite":
        raise ValueError(f"MEMORY_BACKEND must be one of {BACKENDS}, got {backend!r}")

    path = settings.memory_db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    # One shared connection: SqliteSaver serialises access with its own lock,
    # so it is safe across FastAPI's worker threads.
    conn = sqlite3.connect(str(path), check_same_thread=False)
    try:
        saver = SqliteSaver(conn)
        saver.setup()  # creates tables if missing, enables WAL
        yield saver
    finally:
        conn.close()


def count_conversations(saver: BaseCheckpointSaver) -> int:
    """Number of saved conversations (threads). Works for both backends."""
    if isinstance(saver, SqliteSaver):
        with saver.lock:
            row = saver.conn.execute("SELECT COUNT(DISTINCT thread_id) FROM checkpoints").fetchone()
        return int(row[0])
    return len({c.config["configurable"]["thread_id"] for c in saver.list(None)})
