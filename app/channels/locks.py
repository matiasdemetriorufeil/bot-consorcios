"""Per-conversation lock across processes: a Postgres advisory lock.

The processor also keeps a lock in memory (cheap, per process); this one makes it correct with
several uvicorn workers. pg_advisory_xact_lock(namespace, id) is taken on its own connection, in
a transaction that lasts while the message is processed: the bot's session commits several
times per turn and would release a transaction lock on the first commit.
"""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext

from sqlalchemy import Engine, text

ConversationLock = Callable[[int, int], AbstractContextManager[object]]

CHATWOOT_LOCK_NAMESPACE = 1
WHATSAPP_LOCK_NAMESPACE = 2


def advisory_lock(engine: Engine) -> ConversationLock:
    """(namespace, conversation_id) -> context manager holding the lock."""

    @contextmanager
    def lock(namespace: int, conversation_id: int) -> Iterator[None]:
        with engine.connect() as connection, connection.begin():
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:namespace, :key)"),
                {"namespace": namespace, "key": conversation_id},
            )
            yield

    return lock


def no_lock(namespace: int, conversation_id: int) -> AbstractContextManager[object]:
    """For tests that run on one session (and one process)."""
    return nullcontext()
