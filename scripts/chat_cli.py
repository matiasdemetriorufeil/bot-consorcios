"""Chat with the REAL agent over the REAL local database (synced from ConsorPlus), from the
terminal: no WhatsApp. DEVELOPMENT ONLY (requires EMAIL_BACKEND=console).

Usage (from the repo root, with the db running):
    uv run python scripts/chat_cli.py --phone +5493515550977
    uv run python scripts/chat_cli.py --as-owner-of 1 1025

--phone             simulates whoever writes from that number.
--as-owner-of B U   simulates a verified OWNER of unit U of building B (ConsorPlus codes, or
                    text like "Rodas II" "4C"): a test phone +5491100000xxx (not a real line:
                    local numbers never start with 0) is linked to an owner with
                    source="manual" and deleted on exit.

Commands: /reset (new conversation), /exit. When the bot offers options (WhatsApp buttons or
a list) they show as [1] [2] [3]: type the number to "tap" one (the agent gets its title, as
from WhatsApp) or write anything else.

ConsorPlus is only READ: debt comes from app.sync.live.refresh_unit (read-only client) or
the database. Whatever the conversation changes in the simulated phone (e.g. an email
verification) is undone on exit.
"""

import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import delete, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.bot.agent import Agent, AgentReply  # noqa: E402
from app.bot.debt_message import join_blocks  # noqa: E402
from app.bot.identity import to_e164  # noqa: E402
from app.bot.unit_search import SearchStatus, display_building_name, search_unit  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db.models import Building, DataSource, PersonRole, Phone, Unit, UnitPerson  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.llm import Prices, Usage, get_prices, get_provider  # noqa: E402

TEST_PHONE_PREFIX = "+5491100000"  # + 3 digits
TEST_PHONE_RAW = "chat_cli"  # marks the rows this script creates

USE_COLOR = sys.stdout.isatty()


def _paint(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else text


def gray(text: str) -> str:
    return _paint("90", text)


def warn(text: str) -> str:
    return _paint("1;33", text)


class CliError(Exception):
    pass


# --- Simulated owner ----------------------------------------------------------------------


def resolve_unit(session: Session, building: str, unit: str) -> Unit:
    """By ConsorPlus codes (building code + unit combo value) or, failing that, by text."""
    if building.isdigit():
        found = session.scalar(
            select(Unit)
            .join(Building, Building.id == Unit.building_id)
            .where(Building.consorplus_code == int(building), Unit.consorplus_unit_value == unit)
        )
        if found is not None:
            return found
    result = search_unit(session, building, unit)
    if result.status != SearchStatus.FOUND or result.unit is None:
        options = ", ".join(f"{c.building_name} {c.unit_label}" for c in result.candidates)
        raise CliError(f"no encontré una única unidad ({result.status}). {options}")
    return session.get(Unit, result.unit.unit_id)


def remove_leftover_test_phones(session: Session) -> int:
    """Test phones left by a run that could not clean up (killed process)."""
    result = session.execute(
        delete(Phone).where(
            Phone.e164.like(f"{TEST_PHONE_PREFIX}%"),
            Phone.raw == TEST_PHONE_RAW,
            Phone.source == DataSource.MANUAL,
        )
    )
    session.commit()
    return result.rowcount


def create_test_owner_phone(session: Session, unit: Unit) -> Phone:
    owner_id = session.scalar(
        select(UnitPerson.person_id)
        .where(UnitPerson.unit_id == unit.id, UnitPerson.role == PersonRole.OWNER)
        .order_by(UnitPerson.person_id)
        .limit(1)
    )
    if owner_id is None:
        raise CliError("la unidad no tiene propietario cargado")
    taken = set(session.scalars(select(Phone.e164).where(Phone.e164.like(f"{TEST_PHONE_PREFIX}%"))))
    e164 = next(p for n in range(1, 1000) if (p := f"{TEST_PHONE_PREFIX}{n:03d}") not in taken)
    if to_e164(e164) != e164:
        raise CliError(f"el teléfono de prueba {e164} no es válido para identity")
    phone = Phone(
        person_id=owner_id, e164=e164, raw=TEST_PHONE_RAW, source=DataSource.MANUAL, verified=True
    )
    session.add(phone)
    session.commit()
    return phone


# --- Undo changes to a simulated --phone ---------------------------------------------------

_PHONE_FIELDS = ("person_id", "raw", "source", "verified", "needs_review", "conflict")


@dataclass
class PhoneState:
    e164: str
    before: dict | None  # None: the number was not in the database

    @classmethod
    def capture(cls, session: Session, e164: str) -> "PhoneState":
        row = session.scalar(select(Phone).where(Phone.e164 == e164))
        before = {f: getattr(row, f) for f in _PHONE_FIELDS} if row else None
        return cls(e164, before)

    def restore(self, session: Session) -> str | None:
        """Undo what the conversation did to this number. Returns what was done."""
        session.rollback()
        row = session.scalar(select(Phone).where(Phone.e164 == self.e164))
        if row is None:
            return None
        if self.before is None:
            session.delete(row)
            session.commit()
            return "borré el teléfono que quedó vinculado durante la prueba"
        if all(getattr(row, f) == v for f, v in self.before.items()):
            return None
        for field, value in self.before.items():
            setattr(row, field, value)
        session.commit()
        return "restauré el teléfono a como estaba antes de la prueba"


# --- Chat -----------------------------------------------------------------------------------


def describe(reply: AgentReply, prices: Prices) -> list[str]:
    lines = [f"  ↳ {name} → {status}" for name, status in reply.tools_called]
    total = Usage(
        input_tokens=sum(u.input_tokens for u in reply.usage),
        output_tokens=sum(u.output_tokens for u in reply.usage),
        cache_read_tokens=sum(u.cache_read_tokens for u in reply.usage),
        cache_write_tokens=sum(u.cache_write_tokens for u in reply.usage),
    )
    cost = prices.estimate(total)
    lines.append(
        f"  ↳ {len(reply.usage)} llamadas · tokens in {total.input_tokens} · "
        f"out {total.output_tokens} · cache {total.cache_read_tokens}/"
        f"{total.cache_write_tokens} · "
        + (f"US$ {cost:.5f}" if cost is not None else "costo: sin precios en .env")
    )
    if reply.handed_off:
        lines.append("  ↳ derivado a una persona")
    if reply.error:
        lines.append(f"  ↳ error: {reply.error}")
    return lines


def show_options(titles: list[str]) -> str:
    """ "[1] Sí, pasame  [2] No, gracias" """
    return "  ".join(f"[{n}] {title}" for n, title in enumerate(titles, 1))


def pick(text: str, titles: list[str]) -> str:
    """The title of option `text` (a number) as WhatsApp sends a tap; otherwise the text."""
    if text.isdigit() and 1 <= int(text) <= len(titles):
        return titles[int(text) - 1]
    return text


def chat(agent: Agent, session: Session, phone: str, prices: Prices) -> None:
    history: list = []
    titles: list[str] = []  # options of the last reply
    print(gray("Escribí tu mensaje. /reset = conversación nueva, /exit = salir."))
    while True:
        try:
            text = input("\n👤 ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not text:
            continue
        if text == "/exit":
            return
        if text == "/reset":
            history, titles = [], []
            print(gray("— conversación nueva —"))
            continue
        if (chosen := pick(text, titles)) != text:
            print(gray(f"  ↳ tocaste «{chosen}»"))
            text = chosen
        reply = agent.reply(session, phone, text, history)
        history = reply.history
        for line in describe(reply, prices):
            print(gray(line))
        # As in WhatsApp: one message, the blocks built by the code first and then the text.
        print(f"🤖 {join_blocks(reply.debt_messages, reply.text)}")
        titles = [c.title for c in reply.choices]
        if titles:
            print(f"   {show_options(titles)}")


def banner(lines: list[str]) -> None:
    width = max(len(line) for line in lines) + 4
    print(warn("#" * width))
    for line in lines:
        print(warn(f"# {line.ljust(width - 4)} #"))
    print(warn("#" * width))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    who = parser.add_mutually_exclusive_group(required=True)
    who.add_argument("--phone", help="número que escribe, ej. +5493515550977")
    who.add_argument("--as-owner-of", nargs=2, metavar=("EDIFICIO", "UNIDAD"))
    args = parser.parse_args()

    settings = get_settings()
    if settings.email_backend != "console":
        print("chat_cli solo se usa en desarrollo: requiere EMAIL_BACKEND=console.")
        return 2

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    # The console email backend logs the verification email (with the code) at INFO.
    logging.getLogger("app.notify.email").setLevel(logging.INFO)

    provider = get_provider()
    prices = get_prices()
    agent = Agent(provider, prices=prices)  # real refresh_unit: ConsorPlus read-only

    with SessionLocal() as session:
        removed = remove_leftover_test_phones(session)
        if removed:
            print(warn(f"Borré {removed} teléfono(s) de prueba que quedaron de otra corrida."))

        test_phone: Phone | None = None
        state: PhoneState | None = None
        try:
            if args.as_owner_of:
                unit = resolve_unit(session, *args.as_owner_of)
                test_phone = create_test_owner_phone(session, unit)
                phone = test_phone.e164
                banner(
                    [
                        "MODO DESARROLLO: PROPIETARIO SIMULADO",
                        f"Teléfono de prueba {phone} vinculado (source=manual) a un",
                        f"propietario de {display_building_name(unit.building.name)} "
                        f"{unit.label} (unit_id {unit.id}).",
                        "Ve DATOS REALES de esa unidad. Se borra al salir.",
                    ]
                )
            else:
                phone = to_e164(args.phone) or args.phone
                state = PhoneState.capture(session, phone)
            print(gray(f"Proveedor {provider.name} / {provider.model} · escribe {phone}"))
            chat(agent, session, phone, prices)
        except CliError as exc:
            print(f"Error: {exc}")
            return 1
        finally:
            session.rollback()
            if test_phone is not None:
                session.execute(delete(Phone).where(Phone.id == test_phone.id))
                session.commit()
                print(warn(f"Teléfono de prueba {test_phone.e164} borrado."))
            elif state is not None and (done := state.restore(session)):
                print(warn(f"{state.e164}: {done}."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
