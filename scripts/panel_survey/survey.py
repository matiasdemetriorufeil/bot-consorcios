"""Every page of the panel at 1440, 1024 and 390 px over the demo (demo_server.py, invented data),
into docs/panel-capturas/<folder>/ (out of Git), measuring on each:

- how far the page and its tables scroll sideways (.table-responsive: scrollWidth -
  clientWidth). An operator's page that scrolls at any width fails the run (exit code 1), with
  the list; an admin's one is only a warning;
- with --axe, axe-core's violations (downloaded to a temporary folder on each run: never in
  the repository nor in the app).

    uv run python scripts/panel_survey/demo_server.py          (another terminal)
    uv run python scripts/panel_survey/survey.py 5f4 [--axe] [--no-shots]
"""

import argparse
import asyncio
import json
import sys
import tempfile
import urllib.request
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from cdp import Browser, Page  # noqa: E402
from demo_server import PASSWORD, REPO, demo_url  # noqa: E402
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db.models import (  # noqa: E402
    Amenity,
    AmenitySlot,
    Building,
    BuildingInfo,
    Claim,
    ClaimCategory,
    PanelUser,
    Provider,
    QuickReply,
    Reservation,
    SyncRun,
    VerificationRequest,
    WaContact,
    WaConversation,
    WaTemplate,
)

SIZES = (
    ((1440, 900), False, "escritorio"),
    ((1024, 768), False, "1024"),
    ((390, 844), True, "celular"),
)
AXE_URL = "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.2/axe.min.js"
MEASURE = """(function () {
  var tables = 0;
  document.querySelectorAll('.table-responsive').forEach(function (box) {
    tables = Math.max(tables, box.scrollWidth - box.clientWidth);
  });
  var sizes = {};
  document.querySelectorAll('.page-wrapper *').forEach(function (el) {
    if (el.children.length === 0 && el.textContent.trim()) sizes[getComputedStyle(el).fontSize] = 1;
  });
  return JSON.stringify({page: Math.max(0, document.documentElement.scrollWidth - innerWidth),
    tables: Math.max(0, tables), sizes: Object.keys(sizes).sort()});
})()"""
AXE_RUN = """axe.run(document, {resultTypes: ['violations']}).then(function (r) {
  return JSON.stringify(r.violations.map(function (v) { return v.id + ' (' + v.impact + ')'; }));
})"""


def pages() -> list[tuple[str, str, str]]:
    """(name, path, who): who is "op" (an operator's page) or "admin"."""
    engine = create_engine(demo_url())

    def one(stmt: object) -> object:
        with Session(engine) as s:
            return s.scalar(stmt)  # type: ignore[call-overload]

    amenity = one(select(Amenity.id).order_by(Amenity.id))
    free_day = date.today() + timedelta(days=3)
    slot = one(
        select(AmenitySlot.id).where(
            AmenitySlot.amenity_id == amenity, AmenitySlot.weekday == free_day.weekday()
        )
    )
    claim = one(select(Claim.id).join(ClaimCategory).where(ClaimCategory.list_title == "Bomba"))
    conv = one(
        select(WaConversation.id).join(WaContact).where(WaContact.phone_e164 == "+5493515550111")
    )
    building = one(select(Building.id).order_by(Building.id))
    verification = one(select(VerificationRequest.id))
    user = one(select(PanelUser.id).where(PanelUser.username == "marta"))
    book = f"/admin/amenities/{amenity}/book?slot={slot}&date={free_day.isoformat()}"
    reservation = one(select(Reservation.id))
    info = one(select(BuildingInfo.id))
    provider = one(select(Provider.id).order_by(Provider.id))
    category = one(select(ClaimCategory.id).order_by(ClaimCategory.id))
    reply = one(select(QuickReply.id))
    template = one(select(WaTemplate.id))
    run = one(select(SyncRun.id))
    return [
        ("01-pendientes", "/admin/pending", "op"),
        ("02-conversaciones", "/admin/conversations", "op"),
        ("03-conversacion", f"/admin/conversations/{conv}", "op"),
        ("04-conversacion-ejemplo", "/admin/conversations/example?tab=waiting", "op"),
        ("05-reclamos", "/admin/claims", "op"),
        ("06-reclamo", f"/admin/claims/{claim}", "op"),
        ("07-nuevo-reclamo", "/admin/claims/new", "op"),
        ("08-telefonos", "/admin/phones", "op"),
        ("09-verificaciones", "/admin/verifications", "op"),
        ("10-aprobar-verificacion", f"/admin/verifications/{verification}/approve", "op"),
        ("11-sum", "/admin/amenities", "op"),
        ("12-planilla-sum", f"/admin/amenities/{amenity}/week", "op"),
        ("13-reservar-sum", book, "op"),
        ("14-reserva-sum", f"/admin/amenities/{amenity}/reservations/{reservation}", "op"),
        ("15-guia", "/admin/guide", "op"),
        ("20-edificios", "/admin/building/list", "admin"),
        ("21-edificio", f"/admin/building/details/{building}", "admin"),
        ("22-edificio-editar", f"/admin/building/edit/{building}", "admin"),
        ("23-info-edificios", "/admin/building-info/list", "admin"),
        ("24-info-edificio-nueva", "/admin/building-info/create", "admin"),
        ("25-info-edificio-editar", f"/admin/building-info/edit/{info}", "admin"),
        ("26-reclamos-por-edificio", "/admin/building-claims", "admin"),
        ("27-reclamos-de-un-edificio", f"/admin/building-claims/{building}", "admin"),
        ("28-proveedores", "/admin/provider/list", "admin"),
        ("29-proveedor-editar", f"/admin/provider/edit/{provider}", "admin"),
        ("30-tipos-de-problema", "/admin/claim-category/list", "admin"),
        ("31-tipo-de-problema-editar", f"/admin/claim-category/edit/{category}", "admin"),
        ("32-configuracion-reclamos", "/admin/claim-settings/list", "admin"),
        ("33-configuracion-bot", "/admin/bot-settings/list", "admin"),
        ("34-respuestas-rapidas", "/admin/quick-reply/list", "admin"),
        ("35-respuesta-rapida-editar", f"/admin/quick-reply/edit/{reply}", "admin"),
        ("36-plantillas", "/admin/wa-template/list", "admin"),
        ("37-plantilla-editar", f"/admin/wa-template/edit/{template}", "admin"),
        ("38-usuarios", "/admin/users", "admin"),
        ("39-usuario-editar", f"/admin/users/{user}", "admin"),
        ("40-metricas", "/admin/metrics", "admin"),
        ("41-sincronizaciones", "/admin/sync-run/list", "admin"),
        ("42-sincronizacion", f"/admin/sync-run/details/{run}", "admin"),
        ("43-chat-de-prueba", "/admin/dev-chat", "admin"),
        ("44-configuracion-sum", f"/admin/amenities/{amenity}/config", "admin"),
        ("45-pendientes-admin", "/admin/pending", "admin"),
        ("46-error-403", "/admin/users", "op"),
    ]  # fmt: skip


def axe_script() -> str:
    """axe-core, downloaded into a temporary folder (never kept)."""
    with tempfile.TemporaryDirectory(prefix="panel-survey-axe-") as folder:
        path = Path(folder) / "axe.min.js"
        urllib.request.urlretrieve(AXE_URL, path)  # noqa: S310 (a fixed https URL)
        return path.read_text(encoding="utf-8")


async def run(folder: str, axe: bool, shots: bool) -> int:
    out = REPO / "docs" / "panel-capturas" / folder
    axe_js = axe_script() if axe else ""
    failures: list[str] = []
    warnings: list[str] = []
    listed = pages()
    with Browser():
        p = await Page.open()
        for (width, height), mobile, suffix in SIZES:
            await p.send("Network.clearBrowserCookies")
            await p.size(width, height, mobile=mobile)
            await p.go("/admin/login", 1.5)
            if shots:
                await p.shot(out / f"00-ingreso-{suffix}.png", full=False)
        for who, user in (("op", "marta"), ("admin", "jefa")):
            await p.send("Network.clearBrowserCookies")
            await p.size(1440, 900)
            await p.login(user, PASSWORD)
            for name, path, owner in listed:
                if owner != who:
                    continue
                for (width, height), mobile, suffix in SIZES:
                    await p.size(width, height, mobile=mobile)
                    await p.go(path, 1.8)
                    if shots:
                        await p.shot(out / f"{name}-{suffix}.png")
                    measure = json.loads(await p.js(MEASURE))
                    page_px, tables_px = measure["page"], measure["tables"]
                    line = f"{name}@{width}: página {page_px} px, tablas {tables_px} px"
                    print(line, measure["sizes"])
                    if measure["page"] or measure["tables"]:
                        (failures if who == "op" else warnings).append(line)
                    if axe:
                        found = json.loads(await p.js(axe_js + ";" + AXE_RUN))
                        if found:
                            warnings.append(f"{name}@{width}: axe {found}")
        await p.close()
    for line in warnings:
        print("AVISO", line)
    for line in failures:
        print("FALLA", line)
    print("Bien: ninguna pantalla de la operadora se desplaza." if not failures else "")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", help="carpeta dentro de docs/panel-capturas/")
    parser.add_argument("--axe", action="store_true", help="correr axe-core en cada pantalla")
    parser.add_argument("--no-shots", action="store_true", help="solo medir, sin capturas")
    args = parser.parse_args()
    return asyncio.run(run(args.folder, args.axe, not args.no_shots))


if __name__ == "__main__":
    sys.exit(main())
