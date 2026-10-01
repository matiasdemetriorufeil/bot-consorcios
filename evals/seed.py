"""Invented data for the bot evaluation. Nothing here is real.

PHONES maps the aliases used in cases.yaml to the phone that writes. Amounts and payment
codes are repeated literally in cases.yaml (what must / must not appear).
"""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.db.models import (
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

PHONES = {
    "ana": "+5493515550101",  # owner Rodas II 04-C
    "bruno": "+5493515550102",  # owner Rodas II 04-B and Torre del Sol COC.3
    "carla": "+5493515550103",  # owner Torre del Sol 05-A (no payment code)
    "fede": "+5493515550104",  # owner Torre del Sol 05-B (up to date)
    "tito": "+5493515550105",  # TENANT of Rodas II 04-C
    "desconocido": "+5493515550199",  # not in the database
    "desconocido2": "+5493515550198",
}

FETCHED_AT = datetime(2026, 9, 30, 14, 5, tzinfo=ZoneInfo("America/Argentina/Cordoba"))


def _debt(unit: Unit, lines: list[tuple[str, str, str]]) -> DebtSnapshot:
    """lines: (period, concept, balance)."""
    total = sum((Decimal(b) for _, _, b in lines), Decimal(0))
    return DebtSnapshot(
        unit=unit,
        fetched_at=FETCHED_AT,
        source=SyncKind.NIGHTLY,
        total_amount=total,
        is_up_to_date=total <= 0,
        lines=[
            DebtLine(
                concept=concept,
                period=period,
                concept_amount=Decimal(balance),
                balance_due=Decimal(balance),
                accumulated=None,
            )
            for period, concept, balance in lines
        ],
    )


def seed(session: Session) -> None:
    rodas2 = Building(consorplus_code=101, name="101 RODAS II", address="Calle Ficticia 100")
    rodas1 = Building(consorplus_code=102, name="102 RODAS I", address="Calle Ficticia 200")
    sol = Building(consorplus_code=103, name="103 TORRE DEL SOL", address="Av. Inventada 300")
    algarrobos = Building(consorplus_code=104, name="104 LOS ALGARROBOS")

    def unit(building: Building, label: str, code: str | None = None) -> Unit:
        u = Unit(
            building=building, consorplus_unit_value=f"{building.consorplus_code}-{label}",
            label=label, payment_code=code,
        )  # fmt: skip
        session.add(u)
        return u

    r2_4c = unit(rodas2, "04-C", "1111222233334444001")
    r2_4b = unit(rodas2, "04-B", "1111222233334444002")
    r2_1a = unit(rodas2, "01-A", "1111222233334444004")
    unit(rodas2, "PB-LOC", "1111222233334444006")
    r1_4c = unit(rodas1, "04-C", "1111222233334444007")
    r1_2b = unit(rodas1, "02-B", "1111222233334444008")
    sol_5a = unit(sol, "05-A")  # no payment code
    sol_5b = unit(sol, "05-B", "1111222233334444005")
    sol_coc3 = unit(sol, "COC.3", "1111222233334444003")
    alg_2a = unit(algarrobos, "02-A", "1111222233334444009")

    def person(name: str, email: str | None = None, phone: str | None = None) -> Person:
        p = Person(full_name=name, email=email)
        if phone:
            p.phones.append(Phone(e164=phone, source=DataSource.CONSORPLUS))
        session.add(p)
        return p

    ana = person("ANA FICTICIA", "ana.ficticia@example.com", PHONES["ana"])
    bruno = person("BRUNO INVENTADO", "bruno@example.com", PHONES["bruno"])
    carla = person("CARLA PRUEBA", "carla@example.com", PHONES["carla"])
    fede = person("FEDE ALDIA", "fede@example.com", PHONES["fede"])
    tito = person("TITO INQUILINO", "tito@example.com", PHONES["tito"])
    diego = person("DIEGO EJEMPLO", "diego.ejemplo@example.com")  # owner without phone
    elena = person("ELENA SINMAIL")  # owner without email
    gustavo = person("GUSTAVO FICTICIO", "estudiodiegorufeil@gmail.com")  # excluded email
    hugo = person("HUGO RODAS UNO", "hugo@example.com")
    session.flush()

    links = [
        (r2_4c, ana, PersonRole.OWNER),
        (r2_4c, tito, PersonRole.TENANT),
        (r2_4b, bruno, PersonRole.OWNER),
        (sol_coc3, bruno, PersonRole.OWNER),
        (sol_5a, carla, PersonRole.OWNER),
        (sol_5b, fede, PersonRole.OWNER),
        (r2_1a, diego, PersonRole.OWNER),
        (alg_2a, elena, PersonRole.OWNER),
        (r1_2b, gustavo, PersonRole.OWNER),
        (r1_4c, hugo, PersonRole.OWNER),
    ]
    for u, p, role in links:
        session.add(
            UnitPerson(unit_id=u.id, person_id=p.id, role=role, source=DataSource.CONSORPLUS)
        )

    session.add_all(
        [
            _debt(r2_4c, [("08/2026", "EXPENSAS ORDINARIAS", "82530"),
                          ("09/2026", "EXPENSAS ORDINARIAS", "82530")]),
            _debt(r2_4b, [("09/2026", "EXPENSAS ORDINARIAS", "48500")]),
            _debt(sol_coc3, [("09/2026", "EXPENSAS COCHERA", "12300")]),
            _debt(sol_5a, [("09/2026", "EXPENSAS ORDINARIAS", "87250.50")]),
            _debt(sol_5b, []),
            _debt(r2_1a, [("07/2026", "EXPENSAS ORDINARIAS", "76800"),
                          ("08/2026", "EXPENSAS ORDINARIAS", "76800"),
                          ("09/2026", "EXPENSAS ORDINARIAS", "76800")]),
            _debt(alg_2a, [("09/2026", "EXPENSAS ORDINARIAS", "95000")]),
            _debt(r1_2b, [("09/2026", "EXPENSAS ORDINARIAS", "61200")]),
            _debt(r1_4c, [("09/2026", "EXPENSAS ORDINARIAS", "70400")]),
        ]
    )  # fmt: skip
    session.commit()
