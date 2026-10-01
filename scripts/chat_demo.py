"""Real conversation with the configured LLM (LLM_PROVIDER / LLM_MODEL) over the local
database, with INVENTED data created inside a transaction that is rolled back at the end.

Debt comes from the invented stored snapshot: ConsorPlus is never queried. Prints the
conversation, the tool calls and the tokens / estimated cost logged in bot_events.

Usage (from the repo root, with the db running):
    uv run python scripts/chat_demo.py              # scripted conversations
    uv run python scripts/chat_demo.py --interactive
"""

import argparse
import logging
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.bot.agent import Agent  # noqa: E402
from app.db.models import (  # noqa: E402
    BotEvent,
    Building,
    DataSource,
    DebtLine,
    DebtSnapshot,
    Person,
    PersonRole,
    Phone,
    SyncKind,
    Unit,
    UnitPerson,
)
from app.db.queries import get_latest_debt  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.llm import get_prices, get_provider  # noqa: E402
from app.sync.live import DebtResult  # noqa: E402

OWNER_PHONE = "+5493515550142"  # invented
UNKNOWN_PHONE = "+5493515550977"  # invented
OWNER_SCRIPT = [
    "Hola buenas tardes",
    "¿Cuánto debo de expensas?",
    "¿Y cómo lo pago?",
    "No puedo pagar todo junto, ¿puedo hacer un plan de pagos?",
]
UNKNOWN_SCRIPT = [
    "Hola, quiero saber cuánto debo del 7B del Demo Palmeras",
    "Sí, dale",
]


def _seed(session: Session) -> None:
    building = Building(consorplus_code=999001, name="999 DEMO PALMERAS")
    unit = Unit(
        building=building,
        consorplus_unit_value="999001-7B",
        label="07-B",
        payment_code="0000999900001111222",
    )
    owner = Person(full_name="Laura Demo", email="laura.demo@example.com")
    session.add_all([building, unit, owner])
    session.flush()
    session.add(UnitPerson(unit_id=unit.id, person_id=owner.id, role=PersonRole.OWNER,
                           source=DataSource.CONSORPLUS))  # fmt: skip
    session.add(Phone(person_id=owner.id, e164=OWNER_PHONE, source=DataSource.BOT_VERIFIED,
                      verified=True))  # fmt: skip
    session.add(
        DebtSnapshot(
            unit_id=unit.id,
            fetched_at=datetime.now(UTC),
            source=SyncKind.LIVE,
            total_amount=Decimal("165060.00"),
            is_up_to_date=False,
            lines=[
                DebtLine(concept="Expensas ordinarias", period="08/2026",
                         concept_amount=Decimal("82530"), balance_due=Decimal("82530"),
                         accumulated=Decimal("82530")),
                DebtLine(concept="Expensas ordinarias", period="09/2026",
                         concept_amount=Decimal("82530"), balance_due=Decimal("82530"),
                         accumulated=Decimal("165060")),
            ],
        )
    )  # fmt: skip
    session.commit()


def _talk(agent: Agent, session: Session, phone: str, messages: list[str]) -> None:
    history = []
    for text in messages:
        print(f"\n👤 {text}")
        reply = agent.reply(session, phone, text, history)
        history = reply.history
        print(f"🤖 {reply.text}")
        if reply.error:
            print(f"   (error: {reply.error})")


def _interactive(agent: Agent, session: Session, phone: str) -> None:
    history = []
    while True:
        try:
            text = input("\n👤 ")
        except EOFError:
            return
        if not text.strip():
            return
        reply = agent.reply(session, phone, text, history)
        history = reply.history
        print(f"🤖 {reply.text}")


def _report(session: Session, since_id: int) -> None:
    events = session.scalars(
        select(BotEvent).where(BotEvent.id > since_id).order_by(BotEvent.id)
    ).all()
    print("\n--- bot_events ---")
    for e in events:
        p = e.payload
        if e.event_type == "tool_call":
            print(f"tool_call   {p['tool']}({p['args']}) -> {p['status']}")
        elif e.event_type == "handoff":
            print(f"handoff     {p['priority']}: {p['reason']}")
        elif e.event_type == "agent_turn":
            print(
                f"agent_turn  rounds={p['rounds']} in={p['input_tokens']} "
                f"out={p['output_tokens']} cache={p['cache_read_tokens']} "
                f"cost_usd={p['cost_usd']}"
            )
    turns = [e.payload for e in events if e.event_type == "agent_turn"]
    calls = [e.payload for e in events if e.event_type == "llm_usage"]
    total_in = sum(t["input_tokens"] for t in turns)
    total_out = sum(t["output_tokens"] for t in turns)
    total_cache = sum(t["cache_read_tokens"] for t in turns)
    costs = [t["cost_usd"] for t in turns if t["cost_usd"] is not None]
    print(
        f"\nTOTAL: {len(turns)} mensajes, {len(calls)} llamadas al modelo, "
        f"input={total_in} output={total_out} cache_read={total_cache} tokens, "
        f"costo estimado USD {sum(costs):.6f}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--phone", choices=["owner", "unknown"], default="owner")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    provider = get_provider()
    print(f"Proveedor: {provider.name} / modelo: {provider.model}")

    with engine.connect() as connection:
        transaction = connection.begin()
        session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            _seed(session)
            since = session.scalar(select(BotEvent.id).order_by(BotEvent.id.desc())) or 0
            agent = Agent(
                provider,
                prices=get_prices(),
                refresh_debt=lambda uid: DebtResult(get_latest_debt(session, uid), stale=False),
            )
            if args.interactive:
                phone = OWNER_PHONE if args.phone == "owner" else UNKNOWN_PHONE
                _interactive(agent, session, phone)
            else:
                print("\n=== Conversación 1: propietaria verificada ===")
                _talk(agent, session, OWNER_PHONE, OWNER_SCRIPT)
                print("\n=== Conversación 2: número no registrado ===")
                _talk(agent, session, UNKNOWN_PHONE, UNKNOWN_SCRIPT)
            _report(session, since)
        finally:
            session.close()
            transaction.rollback()  # nothing of the demo stays in the database
    return 0


if __name__ == "__main__":
    sys.exit(main())
