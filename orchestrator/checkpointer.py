"""LangGraph Postgres checkpointer on the same database. thread_id is the run id, so a retry resumes.

Tables are created by db.migrate (as the migration owner); the worker only uses them."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from langgraph.checkpoint.postgres import PostgresSaver

from db.connection import database_url


@contextmanager
def checkpointer(url: str | None = None) -> Iterator[PostgresSaver]:
    with PostgresSaver.from_conn_string(url or database_url()) as saver:
        yield saver
