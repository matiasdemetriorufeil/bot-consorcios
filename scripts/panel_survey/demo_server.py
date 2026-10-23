"""The panel alone over a separate demo database (<db>_panel_demo) with INVENTED data: for the
panel's screenshots and measures (survey.py). Only for development.

    uv run python scripts/panel_survey/demo_server.py [--empty] [--production] [--port 8765]
    uv run python scripts/panel_survey/demo_server.py --drop

Safety (guard.py): it only creates, fills or drops a database whose name ends in _panel_demo,
and its settings force the ConsorPlus credentials empty: it never connects to ConsorPlus. The
WhatsApp client is a fake one: nothing leaves the machine.
"""

import argparse
import os
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).parent))

import uvicorn  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from guard import NO_CONSORPLUS, check_demo_database, consorplus_in  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.admin import setup_admin  # noqa: E402
from app.admin.auth import hash_password  # noqa: E402
from app.admin.neighbors import NeighborsView  # noqa: E402
from app.claims.service import (  # noqa: E402
    Reporter,
    close_claim,
    create_claim,
    mark_acknowledged,
    mark_sent,
    provider_declined,
)
from app.config import Settings  # noqa: E402
from app.consorplus.errors import ConsorPlusUnavailableError  # noqa: E402
from app.db.models import (  # noqa: E402
    Amenity,
    AmenitySlot,
    Building,
    BuildingClaimCategory,
    BuildingInfo,
    BuildingInfoCategory,
    ClaimActor,
    ClaimCategory,
    ClaimScope,
    ClaimSource,
    ClaimStatus,
    ClosedDay,
    ClosedDayKind,
    DataSource,
    PanelRole,
    PanelUser,
    Person,
    PersonRole,
    Phone,
    Provider,
    QuickReply,
    Reservation,
    ReservationSource,
    RosterSuppression,
    SuppressionKind,
    SyncJob,
    SyncKind,
    SyncRun,
    SyncStatus,
    Unit,
    UnitPerson,
    VerificationRequest,
    WaAuthor,
    WaContact,
    WaConversation,
    WaConversationStatus,
    WaDirection,
    WaMessage,
    WaMessageStatus,
    WaTemplate,
)

PASSWORD = "clave-demo-inventada-123"  # noqa: S105 (invented: only the demo's users)
TZ = ZoneInfo("America/Argentina/Cordoba")


def demo_url() -> str:
    """The demo database: the configured one's name plus _panel_demo (never the real one)."""
    url = make_url(Settings(_env_file=REPO / ".env").database_url)
    demo = url.set(database=f"{url.database}_panel_demo")
    check_demo_database(demo.database)
    return demo.render_as_string(hide_password=False)


def drop(url: str) -> None:
    target = make_url(url)
    check_demo_database(target.database)
    server = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with server.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{target.database}" WITH (FORCE)'))
        gone = not c.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": target.database}
        ).scalar()
    server.dispose()
    print("Base de demo borrada." if gone else "La base de demo sigue ahí.")


def recreate(url: str) -> None:
    target = make_url(url)
    check_demo_database(target.database)
    server = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with server.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{target.database}" WITH (FORCE)'))
        c.execute(text(f'CREATE DATABASE "{target.database}"'))
    server.dispose()
    config = Config(str(REPO / "alembic.ini"))
    config.set_main_option("script_location", str(REPO / "alembic"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, "head")


_wamid = iter(range(1, 100000))


def conv(s: Session, wa: str, name: str, status: WaConversationStatus, **values) -> WaConversation:
    contact = WaContact(phone_e164=f"+{wa}", wa_id=wa, profile_name=name)
    s.add(contact)
    s.flush()
    row = WaConversation(
        contact_id=contact.id, status=status, last_inbound_at=datetime.now(UTC), **values
    )
    s.add(row)
    s.flush()
    return row


def msg(s: Session, c: WaConversation, body: str, minutes: int, author=WaAuthor.CONTACT, **v):
    inbound = author == WaAuthor.CONTACT
    s.add(
        WaMessage(
            conversation_id=c.id,
            direction=WaDirection.INBOUND if inbound else WaDirection.OUTBOUND,
            author=author,
            message_type=v.pop("message_type", "text"),
            body=body,
            wa_message_id=f"wamid.demo.{next(_wamid)}",
            status=WaMessageStatus.RECEIVED if inbound else WaMessageStatus.SENT,
            created_at=datetime.now(UTC) - timedelta(minutes=minutes),
            **v,
        )
    )
    s.flush()


def seed(s: Session, empty: bool) -> None:
    now = datetime.now(UTC)
    for username, name, role in (
        ("marta", "Marta Ejemplo", PanelRole.OPERATOR),
        ("jefa", "Jefa Ejemplo", PanelRole.ADMIN),
    ):
        s.add(
            PanelUser(
                username=username,
                display_name=name,
                password_hash=hash_password(PASSWORD),
                role=role,
                active=True,
                tour_seen_at=now,
            )
        )
    torre = Building(consorplus_code=901, name="901 TORRE EJEMPLO", active=True)
    ficticio = Building(consorplus_code=902, name="902 EDIFICIO FICTICIO", active=True)
    s.add_all([torre, ficticio])
    s.flush()
    unit = Unit(building=torre, consorplus_unit_value="1A", label="1A", active=True)
    unit2 = Unit(building=ficticio, consorplus_unit_value="3B", label="3B", active=True)
    s.add_all([unit, unit2])
    s.flush()
    ana = Person(full_name="Ana Ejemplo")
    s.add(ana)
    s.flush()
    s.add(
        UnitPerson(
            unit_id=unit.id, person_id=ana.id, role=PersonRole.OWNER, source=DataSource.CONSORPLUS
        )
    )
    # SUM: two today, one tomorrow (also when "empty": it is only information).
    today = now.astimezone(TZ).date()
    for building, u, booked in ((torre, unit, (0, 0, 1)), (ficticio, unit2, ())):
        amenity = Amenity(building_id=building.id)
        slots = [AmenitySlot(weekday=d, start_time=time(14), end_time=time(18)) for d in range(7)]
        late = [AmenitySlot(weekday=d, start_time=time(19), end_time=time(23)) for d in range(7)]
        amenity.slots = slots + late
        s.add(amenity)
        s.flush()
        used: set[tuple[int, int]] = set()
        for days in booked:
            day = today + timedelta(days=days)
            slot = slots[day.weekday()] if (days, 0) not in used else late[day.weekday()]
            used.add((days, 0))
            s.add(
                Reservation(
                    amenity_id=amenity.id,
                    slot_id=slot.id,
                    date=day,
                    unit_id=u.id,
                    source=ReservationSource.PANEL,
                )
            )
    if empty:
        s.add(
            SyncRun(
                kind=SyncKind.NIGHTLY,
                job=SyncJob.DEBT,
                status=SyncStatus.OK,
                started_at=now - timedelta(hours=8),
                finished_at=now - timedelta(hours=8),
            )
        )
        s.commit()
        return

    # Conversations waiting: one urgent; the oldest 40 minutes ago.
    for wa, name, minutes, urgent, reason in (
        ("5493515550101", "Ana Ejemplo", 40, False, "person_requested"),
        ("5493515550102", "Beto Inventado", 12, True, "emergency"),
        ("5493515550103", "Carla Ficticia", 3, False, "debt_claim"),
    ):
        c = conv(
            s,
            wa,
            name,
            WaConversationStatus.WAITING_HUMAN,
            handed_off_at=now - timedelta(minutes=minutes),
            handoff_reason=reason,
            handoff_priority="urgent" if urgent else "normal",
        )
        msg(s, c, "Hola, necesito hablar con alguien", minutes + 1)
    # Marta's: two unanswered, one answered.
    for wa, name, answered in (
        ("5493515550111", "Diego Ejemplo", False),
        ("5493515550112", "Elena Inventada", False),
        ("5493515550113", "Fabio Ficticio", True),
    ):
        c = conv(
            s,
            wa,
            name,
            WaConversationStatus.HUMAN,
            assigned_to="marta",
            handed_off_at=now - timedelta(hours=2),
        )
        msg(s, c, "¿Me confirmás el pago?", 30)
        if answered:
            msg(s, c, "Sí, ya figura.", 20, author=WaAuthor.OPERATOR, operator="marta")
    # What the bot knows of a building, and the inbox's quick replies.
    s.add_all(
        [
            BuildingInfo(
                building_id=torre.id,
                title="Horario del SUM",
                category=BuildingInfoCategory.HORARIOS,
                content="De 9 a 24. Se reserva por WhatsApp o en la administración.",
            ),
            BuildingInfo(
                building_id=torre.id,
                title="Mascotas",
                category=BuildingInfoCategory.REGLAMENTO,
                content="Se permiten mascotas con correa en los espacios comunes.",
            ),
            QuickReply(
                title="Saludo", content="Hola, te escribo del Estudio Diego Rufeil.", sort_order=1
            ),
            QuickReply(
                title="Código de pago",
                content="Podés pagar con el código que figura en tu boleta.",
                sort_order=2,
            ),
            # «Días cerrados»: one closed by hand and a holiday the studio opens anyway.
            ClosedDay(day=date(2026, 12, 24), reason="Cierre del estudio"),
            ClosedDay(
                day=date(2026, 11, 23),
                kind=ClosedDayKind.OPEN_ANYWAY,
                reason="Día de la Soberanía Nacional",
            ),
        ]
    )
    # The window of 24 h closed: only a prepared message (WaTemplate) can go.
    s.add(
        WaTemplate(
            name="retomar_conversacion",
            label="Retomar la charla",
            body="Hola, te escribimos del Estudio Diego Rufeil por tu consulta.",
        )
    )
    closed = conv(
        s,
        "5493515550114",
        "Dario Ejemplo",
        WaConversationStatus.HUMAN,
        assigned_to="marta",
        handed_off_at=now - timedelta(days=2),
    )
    closed.last_inbound_at = now - timedelta(days=2)
    msg(s, closed, "Gracias!", 60 * 48)
    # What the bot could not answer this week.
    for wa in ("5493515550121", "5493515550122"):
        c = conv(
            s,
            wa,
            "Vecino Inventado",
            WaConversationStatus.RESOLVED,
            handoff_reason="no_answer",
            handed_off_at=now - timedelta(hours=20),
        )
        msg(s, c, "¿Se puede tener mascotas?", 1300)
    # Phones to review, a pending verification.
    for n, phone in enumerate(("+5493515550131", "+5493515550132")):
        p = Person(full_name=f"Persona Ejemplo {n}")
        s.add(p)
        s.flush()
        s.add(
            Phone(
                person_id=p.id, e164=phone, raw=phone[-7:], source=DataSource.CONSORPLUS,
                needs_review=True,
            )
        )  # fmt: skip
    s.add(
        VerificationRequest(
            phone_e164="+5493515550141",
            unit_id=unit.id,
            claimed_name="Gabriela Ejemplo",
            created_at=now - timedelta(days=3),
        )
    )
    # Claims in every status. Ascensores Ejemplo has WhatsApp; Herrería Inventada does not.
    cats = {}
    for order, (key, name, title, urgent) in enumerate(
        (
            ("lift", "No funciona el ascensor", "Ascensor", True),
            ("damp", "Humedad en un espacio común", "Humedad", False),
            ("gate", "El portón no abre", "Portón", False),
            ("light", "Luz quemada en un pasillo", "Luz", False),
            ("water", "Pérdida de agua", "Agua", False),
            ("pump", "No hay agua", "Bomba", False),
        ),
        start=1,
    ):
        cats[key] = ClaimCategory(
            name=name, list_title=title, scope=ClaimScope.BUILDING, urgent=urgent, sort_order=order
        )
    s.add_all(cats.values())
    s.flush()
    lifts = Provider(name="Ascensores Ejemplo", whatsapp_e164="+5493515550161")
    smith = Provider(name="Herrería Inventada")  # no WhatsApp
    plumber = Provider(name="Plomería Ficticia", whatsapp_e164="+5493515550162")
    s.add_all([lifts, smith, plumber])
    s.flush()
    assigned = {
        "lift": lifts,
        "gate": smith,
        "water": plumber,
        "pump": plumber,
        "damp": None,
        "light": None,
    }
    for b in (torre, ficticio):
        for key, c in cats.items():
            p = assigned[key]
            s.add(
                BuildingClaimCategory(
                    building_id=b.id,
                    category_id=c.id,
                    provider_id=p.id if p else None,
                )
            )
    # Six more in TORRE EJEMPLO only: 12 enabled, so «Problemas y proveedores» shows where
    # WhatsApp's first list ends («Más opciones»).
    for order, (name, title) in enumerate(
        (
            ("Ruidos molestos", "Ruidos"),
            ("Portero eléctrico roto", "Portero"),
            ("Pérdida de gas", "Gas"),
            ("Basura acumulada", "Basura"),
            ("Pintura dañada", "Pintura"),
            ("Plaga en el edificio", "Plagas"),
        ),
        start=len(cats) + 1,
    ):
        extra = ClaimCategory(
            name=name, list_title=title, scope=ClaimScope.BUILDING, sort_order=order
        )
        s.add(extra)
        s.flush()
        s.add(BuildingClaimCategory(building_id=torre.id, category_id=extra.id))
    s.flush()

    def claim(key, phone, hours, building=torre, name="Vecina Ejemplo"):
        return create_claim(
            s,
            building_id=building.id,
            category_id=cats[key].id,
            unit_id=None,
            description="No anda desde la mañana",
            reporter=Reporter(name=name, phone_e164=phone, unit_id=None),
            source=ClaimSource.PANEL,
            now=now - timedelta(hours=hours),
        ).claim

    claim("lift", "+5493515550151", 1)  # pending_send, provider with WhatsApp, urgent
    claim("gate", "+5493515550153", 5)  # pending_send, provider without WhatsApp
    damp = claim("damp", "+5493515550152", 30)  # the studio by design, with an alert
    damp.attention = "stale"
    water = claim("water", "+5493515550154", 4, name="Hugo Ejemplo")  # sent, waiting
    mark_sent(s, water, text="Avisado", actor=ClaimActor.SYSTEM, now=now - timedelta(hours=2))
    pump = claim("pump", "+5493515550155", 26, ficticio, "Inés Ficticia")  # acknowledged
    mark_sent(s, pump, text="Avisado", actor=ClaimActor.SYSTEM, now=now - timedelta(hours=25))
    mark_acknowledged(s, pump, now=now - timedelta(hours=24))
    light = claim("light", "+5493515550156", 50, ficticio)  # solved
    close_claim(
        s,
        light,
        ClaimStatus.SOLVED,
        "Cambiaron la lámpara",
        actor=ClaimActor.PANEL,
        now=now - timedelta(hours=20),
    )
    declined = claim("lift", "+5493515550157", 6, ficticio, "Juan Ejemplo")
    mark_sent(s, declined, text="Avisado", actor=ClaimActor.SYSTEM, now=now - timedelta(hours=5))
    provider_declined(s, declined, now=now - timedelta(hours=4))
    # Ana: a phone of hers (her conversation is identified) and a second unit.
    s.add(
        Phone(person_id=ana.id, e164="+5493515550101", source=DataSource.CONSORPLUS, verified=True)
    )
    s.add(
        UnitPerson(
            unit_id=unit2.id, person_id=ana.id, role=PersonRole.TENANT, source=DataSource.CONSORPLUS
        )
    )
    neighbors(s, torre, ana)
    # Last night's reading of ConsorPlus: one with errors.
    s.add_all(
        [
            SyncRun(
                kind=SyncKind.NIGHTLY,
                job=SyncJob.DEBT,
                status=SyncStatus.OK,
                started_at=now - timedelta(hours=8),
                finished_at=now - timedelta(hours=8),
            ),
            SyncRun(
                kind=SyncKind.NIGHTLY,
                job=SyncJob.ROSTER,
                status=SyncStatus.PARTIAL,
                started_at=now - timedelta(hours=9),
                finished_at=now - timedelta(hours=9),
                units_ok=40,
                units_failed=2,
            ),
        ]
    )
    s.commit()


class DemoNoConsorPlus:
    """The roster source of the demo: ConsorPlus is never reached."""

    def list_buildings(self) -> list:
        return []

    def list_roster(self, building_code: str) -> list:
        raise ConsorPlusUnavailableError("demo: sin ConsorPlus")


def neighbors(s: Session, torre: Building, ana: Person) -> None:
    """Vecinos of TORRE EJEMPLO (invented): the three roles and the three origins, a unit
    without anyone, a phone to review, one in conflict and two things removed by hand."""
    cp, manual, bot = DataSource.CONSORPLUS, DataSource.MANUAL, DataSource.BOT_VERIFIED
    units = {
        value: Unit(building=torre, consorplus_unit_value=value, label=value, active=True)
        for value in ("2B", "3C", "4D", "10A")
    }
    s.add_all(units.values())
    s.flush()

    def person(name: str, *phones: tuple[str, DataSource, dict]) -> Person:
        p = Person(full_name=name, consorplus_name=name)
        s.add(p)
        s.flush()
        for e164, source, flags in phones:
            s.add(Phone(person_id=p.id, e164=e164, source=source, **flags))
        return p

    def link(unit: str, p: Person, role: PersonRole, source: DataSource) -> None:
        s.add(UnitPerson(unit_id=units[unit].id, person_id=p.id, role=role, source=source))

    bruno = person("Bruno Ejemplo", ("+5493515550201", manual, {"verified": True}))
    carla = person("Carla Ejemplo", ("+5493515550202", bot, {"verified": True}))
    rita = person("Rita Ejemplo")
    dario = person(
        "Dario Ejemplo", ("+5493515550203", cp, {"needs_review": True, "raw": "155550203"})
    )
    # In conflict: ConsorPlus also lists it for Dario («También figura en …» in Teléfonos).
    elsa = person(
        "Elsa Ejemplo",
        ("+5493515550204", cp, {"conflict": True, "conflict_person_id": dario.id}),
        ("+5493515550205", cp, {}),
    )
    link("2B", bruno, PersonRole.OWNER, manual)
    link("2B", carla, PersonRole.TENANT, bot)
    link("2B", rita, PersonRole.RESIDENT, manual)
    link("3C", dario, PersonRole.OWNER, cp)
    link("4D", elsa, PersonRole.OWNER, cp)
    link("4D", ana, PersonRole.RESIDENT, manual)
    s.add_all(
        [
            RosterSuppression(
                kind=SuppressionKind.LINK, unit_id=units["3C"].id, role=PersonRole.TENANT,
                name_key="ejemplo ex", building_id=torre.id, person_name="Ex Inquilino Ejemplo",
                created_by="jefa",
            ),
            RosterSuppression(
                kind=SuppressionKind.PHONE, e164="+5493515550299", name_key="bruno ejemplo",
                building_id=torre.id, person_name="Bruno Ejemplo", created_by="jefa",
            ),
        ]
    )  # fmt: skip


def demo_settings(url: str, production: bool) -> Settings:
    """The demo's settings: never ConsorPlus (NO_CONSORPLUS beats the environment)."""
    return Settings(
        _env_file=None,
        database_url=url,
        app_env="production" if production else "development",
        admin_username="rescate",
        admin_password_hash=hash_password(PASSWORD),
        admin_secret_key="clave-de-sesion-demo-inventada-0123456789",  # noqa: S106 (invented)
        **NO_CONSORPLUS,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--empty", action="store_true", help="sin conversaciones ni reclamos")
    parser.add_argument("--production", action="store_true", help="sin «Desarrollo» (la guía)")
    parser.add_argument("--drop", action="store_true", help="borrar la base de demo y salir")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    url = demo_url()
    if args.drop:
        drop(url)
        return
    if found := consorplus_in(os.environ):
        print(
            f"Aviso: {', '.join(found)} en el entorno: la demo las ignora (nunca usa ConsorPlus)."
        )
    recreate(url)
    engine = create_engine(url)
    with Session(engine) as s:
        seed(s, args.empty)
    app = FastAPI()
    # «Actualizar desde ConsorPlus» in the demo: never ConsorPlus (it answers «no se pudo
    # leer»), with the demo's own lock.
    NeighborsView.roster_source = DemoNoConsorPlus()
    NeighborsView.lock_engine = engine
    from tests.whatsapp.fakes import FakeWhatsApp  # invented sends: nothing leaves the machine

    fake = FakeWhatsApp()
    setup_admin(
        app,
        sessionmaker(bind=engine),
        demo_settings(url, args.production),
        sender_factory=lambda: fake,
        bot_factory=lambda: None,
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
