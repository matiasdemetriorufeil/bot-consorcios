"""The per-conversation Postgres advisory lock, with real connections to the test database
(as two uvicorn workers would have)."""

import threading
import time

from sqlalchemy import Engine

from app.channels.locks import WHATSAPP_LOCK_NAMESPACE, advisory_lock

WA = WHATSAPP_LOCK_NAMESPACE
OTHER_NAMESPACE = 99


def _run_both(engine: Engine, first: tuple[int, int], second: tuple[int, int]) -> list[str]:
    lock = advisory_lock(engine)
    order: list[str] = []
    holding = threading.Event()

    def one() -> None:
        with lock(*first):
            order.append("one in")
            holding.set()
            time.sleep(0.3)
            order.append("one out")

    def two() -> None:
        holding.wait(5)
        with lock(*second):
            order.append("two in")

    threads = [threading.Thread(target=one), threading.Thread(target=two)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    return order


def test_same_conversation_waits(db_engine: Engine) -> None:
    order = _run_both(db_engine, (WA, 7), (WA, 7))

    assert order == ["one in", "one out", "two in"]


def test_other_conversations_and_namespaces_do_not_wait(db_engine: Engine) -> None:
    other_conversation = _run_both(db_engine, (WA, 7), (WA, 8))
    other_namespace = _run_both(db_engine, (WA, 7), (OTHER_NAMESPACE, 7))

    assert other_conversation == ["one in", "two in", "one out"]
    assert other_namespace == ["one in", "two in", "one out"]
