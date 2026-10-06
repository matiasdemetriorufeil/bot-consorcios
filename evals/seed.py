"""Invented data for the bot evaluation. Nothing here is real.

PHONES maps the aliases used in cases.yaml to the phone that writes. Amounts and payment
codes are repeated literally in cases.yaml (what must / must not appear).
"""

from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.db.models import (
    Amenity,
    AmenitySlot,
    Building,
    BuildingInfo,
    BuildingInfoCategory,
    DataSource,
    DebtLine,
    DebtSnapshot,
    Person,
    PersonRole,
    Phone,
    Reservation,
    ReservationSource,
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
    "elena": "+5493515550106",  # owner Los Algarrobos 02-A (building with no texts)
    "desconocido": "+5493515550199",  # not in the database
    "desconocido2": "+5493515550198",
}

FETCHED_AT = datetime(2026, 9, 30, 14, 5, tzinfo=ZoneInfo("America/Argentina/Cordoba"))

# Building texts, as an operator would load them in the admin panel. Rodas II says nothing
# about pets nor a pool: questions about them have no answer. Los Algarrobos has no texts.
RODAS2_RULES = """\
Artículo 1 - Ruidos: entre las 22 y las 8 h se debe guardar silencio. No se permite música \
fuerte ni trabajos ruidosos en ese horario.
Artículo 2 - Mudanzas: se hacen de lunes a viernes de 9 a 18 h y los sábados de 9 a 13 h. \
Hay que avisar al encargado con 48 horas de anticipación.
Artículo 3 - Residuos: la basura se saca en bolsas cerradas al contenedor del subsuelo, de \
20 a 22 h.
Artículo 4 - SUM: se reserva con el encargado y se puede usar hasta la 1 de la madrugada.
"""
RODAS2_CONTACTS = """\
Encargado: en la portería de planta baja, de lunes a viernes de 8 a 16 h. Se lo llama desde \
el portero eléctrico, interno 10.
"""
RODAS1_HOURS = "Encargado: de lunes a sábados de 7 a 14 h, en la portería."

# Torre del Sol: a long rules text (well over the 8,000-token budget of get_building_info),
# so only the relevant articles reach the model.
_SOL_ARTICLES = [
    (
        "Mascotas",
        "Se permiten mascotas domésticas. En los espacios comunes deben circular "
        "con correa y por el ascensor de servicio.",
    ),
    (
        "Mudanzas",
        "Las mudanzas se hacen de lunes a viernes de 8 a 17 h, solo por el ascensor "
        "de servicio y con aviso a la administración con 72 horas de anticipación.",
    ),
    (
        "Pileta",
        "La pileta abre del 1 de diciembre al 31 de marzo, de 10 a 20 h. Cada unidad "
        "puede llevar hasta dos invitados.",
    ),
    ("SUM", "El salón de usos múltiples se reserva en portería y se puede usar hasta las 23 h."),
    (
        "Ropa en balcones",
        "Está prohibido tender ropa en los balcones o ventanas que den al "
        "frente del edificio. Se puede usar el tendedero de la terraza de 8 a 20 h.",
    ),
    (
        "Aires acondicionados",
        "Los equipos de aire acondicionado solo se instalan en el "
        "contrafrente, con el desagüe conectado a la cañería pluvial. En el frente no se permiten.",
    ),
    (
        "Obras",
        "Las obras y refacciones dentro de las unidades se hacen de lunes a viernes de "
        "9 a 13 h y de 15 a 18 h, con aviso previo a la administración.",
    ),
]
_SOL_FILLER = (
    "Disposiciones generales. Los copropietarios y ocupantes deben respetar el destino de las "
    "unidades y de las partes comunes, conservar el buen estado del edificio y cumplir las "
    "resoluciones de la asamblea. Cualquier daño a las partes comunes será reparado a cargo "
    "de quien lo cause. Las comunicaciones a la administración se hacen por escrito. "
)


def _sol_rules() -> str:
    """40 generic articles (~13,000 tokens) with the specific ones spread among them."""
    articles = [("Disposiciones generales", _SOL_FILLER * 3)] * 40
    for offset, article in enumerate(_SOL_ARTICLES):
        articles.insert(5 + offset * 6, article)
    return "\n".join(
        f"Artículo {n} - {title}: {text_}" for n, (title, text_) in enumerate(articles, start=1)
    )


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
    elena = person("ELENA SINMAIL", None, PHONES["elena"])  # owner without email
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
            BuildingInfo(building=rodas2, title="Reglamento interno", content=RODAS2_RULES,
                         category=BuildingInfoCategory.REGLAMENTO),
            BuildingInfo(building=rodas2, title="Contacto del encargado",
                         content=RODAS2_CONTACTS, category=BuildingInfoCategory.CONTACTOS),
            BuildingInfo(building=rodas1, title="Horario del encargado", content=RODAS1_HOURS,
                         category=BuildingInfoCategory.HORARIOS),
            BuildingInfo(building=sol, title="Reglamento de copropiedad", content=_sol_rules(),
                         category=BuildingInfoCategory.REGLAMENTO),
        ]
    )  # fmt: skip
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
    _amenities(session, rodas2, sol, r2_4c, r2_4b)
    session.commit()


# SUM: Rodas II books through the bot; Torre del Sol only through the studio; the others have
# none. The default "now" of the cases is Thursday 01/10/2026 11:00.
RODAS2_SUM_RULES = (
    "La música tiene que terminar a la 1 de la mañana. El salón se entrega limpio: la limpieza "
    "corre por cuenta de quien reserva. Capacidad máxima: 40 personas."
)


def _amenities(session: Session, rodas2: Building, sol: Building, r2_4c: Unit, r2_4b: Unit) -> None:
    night = {"start_time": time(20), "end_time": time(2), "label": "Noche"}
    noon = {"start_time": time(12), "end_time": time(17), "label": "Mediodía"}
    rodas2_sum = Amenity(
        building=rodas2,
        bot_booking_enabled=True,
        rules_text=RODAS2_SUM_RULES,
        slots=[
            AmenitySlot(weekday=4, **night),
            AmenitySlot(weekday=5, **night),
            AmenitySlot(weekday=5, **noon),
            AmenitySlot(weekday=6, **noon),
        ],
    )
    sol_sum = Amenity(
        building=sol,
        bot_booking_enabled=False,
        rules_text="Se reserva por la administración. Música hasta las 23.",
        slots=[AmenitySlot(weekday=5, start_time=time(12), end_time=time(18))],
    )
    session.add_all([rodas2_sum, sol_sum])
    session.flush()
    friday_night, saturday_night = rodas2_sum.slots[0], rodas2_sum.slots[1]
    session.add_all(
        [
            # Ana's, for tomorrow: 33 h ahead, too late to cancel (48 h).
            Reservation(amenity=rodas2_sum, slot=friday_night, date=date(2026, 10, 2),
                        unit=r2_4c, source=ReservationSource.BOT),
            # Bruno's: Saturday 03/10 night is taken.
            Reservation(amenity=rodas2_sum, slot=saturday_night, date=date(2026, 10, 3),
                        unit=r2_4b, source=ReservationSource.PANEL),
        ]
    )  # fmt: skip
