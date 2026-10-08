"""Invented providers and kinds of problem for the claims' tests."""

from itertools import count

from sqlalchemy.orm import Session

from app.db.models import BuildingClaimCategory, ClaimCategory, ClaimScope, Provider

_orders = count(1)


def category(
    session: Session,
    name: str,
    *,
    list_title: str | None = None,
    scope: ClaimScope = ClaimScope.BUILDING,
    active: bool = True,
    sort_order: int | None = None,
) -> ClaimCategory:
    c = ClaimCategory(
        name=name,
        list_title=list_title or name[:24],
        scope=scope,
        active=active,
        sort_order=sort_order if sort_order is not None else next(_orders) * 10,
    )
    session.add(c)
    session.flush()
    return c


def provider(
    session: Session, name: str, whatsapp: str | None = None, *, active: bool = True
) -> Provider:
    p = Provider(name=name, whatsapp_e164=whatsapp, active=active)
    session.add(p)
    session.flush()
    return p


def assign(
    session: Session,
    building_id: int,
    c: ClaimCategory,
    p: Provider | None = None,
    *,
    enabled: bool = True,
    sort_order: int | None = None,
) -> BuildingClaimCategory:
    row = BuildingClaimCategory(
        building_id=building_id,
        category_id=c.id,
        enabled=enabled,
        provider_id=p.id if p else None,
        sort_order=c.sort_order if sort_order is None else sort_order,
    )
    session.add(row)
    session.flush()
    return row
