"""Panel users: login with the users of panel_users, roles and what an operator can open,
the "Usuarios" page (admins only). Invented names and passwords."""

import pytest
from sqlalchemy import select

from app.admin.auth import LOCKOUT_SECONDS, MAX_FAILED_LOGINS, verify_password
from app.db.models import PanelRole, PanelUser
from tests.admin.conftest import (
    ADMIN,
    OPERATOR,
    TABLE_ADMIN,
    USER_PASSWORD,
    Panel,
    make_user,
)

NEW_PASSWORD = "otra-clave-de-usuaria-inventada"
ADMIN_ONLY_PAGES = [
    "/admin/users",
    "/admin/bot-settings/list",
    "/admin/building/list",
    "/admin/building-info/list",
    "/admin/sync-run/list",
    "/admin/metrics",
    "/admin/wa-template/list",
    "/admin/quick-reply/list",
]
OPERATOR_PAGES = [
    "/admin/conversations",
    "/admin/phones/list",
    "/admin/phone/list",
    "/admin/verification-request/list",
    "/admin/amenities",
    "/admin/claims",
]


def _user(panel: Panel, username: str) -> PanelUser:
    panel.session.expire_all()
    user = panel.session.scalar(select(PanelUser).where(PanelUser.username == username))
    assert user is not None
    return user


# --- Login ------------------------------------------------------------------------------------


def test_a_table_user_logs_in_case_insensitive(panel: Panel) -> None:
    make_user(panel.session, OPERATOR)

    assert panel.login("Marta ", USER_PASSWORD).status_code == 302
    assert panel.client.get("/admin/conversations").status_code == 200
    assert _user(panel, OPERATOR).last_login_at is not None


def test_wrong_password_of_a_table_user(panel: Panel) -> None:
    make_user(panel.session, OPERATOR)

    assert panel.login(OPERATOR, "clave-equivocada").status_code == 400


def test_an_inactive_user_cannot_log_in(panel: Panel) -> None:
    make_user(panel.session, OPERATOR, active=False)

    assert panel.login(OPERATOR, USER_PASSWORD).status_code == 400


def test_lockout_is_the_same_for_table_users(panel: Panel) -> None:
    make_user(panel.session, OPERATOR)
    for _ in range(MAX_FAILED_LOGINS - 1):
        assert panel.login(OPERATOR, "mal").status_code == 400
    assert panel.login(OPERATOR, "mal").status_code == 429
    assert panel.login(OPERATOR, USER_PASSWORD).status_code == 429  # even the right one

    panel.clock.advance(LOCKOUT_SECONDS + 1)
    assert panel.login(OPERATOR, USER_PASSWORD).status_code == 302


def test_deactivating_ends_the_open_session(panel: Panel) -> None:
    user = make_user(panel.session, OPERATOR)
    assert panel.login(OPERATOR, USER_PASSWORD).status_code == 302

    user.active = False
    user.session_version += 1
    panel.session.commit()

    response = panel.client.get("/admin/conversations", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].endswith("/admin/login")


def test_the_env_user_is_still_admin(logged_in: Panel) -> None:
    assert logged_in.client.get("/admin/users").status_code == 200
    assert logged_in.client.get("/admin/bot-settings/list").status_code == 200


# --- Roles ------------------------------------------------------------------------------------


@pytest.mark.parametrize("url", ADMIN_ONLY_PAGES)
def test_an_operator_cannot_open_admin_pages(operator: Panel, url: str) -> None:
    assert operator.client.get(url).status_code == 403


@pytest.mark.parametrize("url", OPERATOR_PAGES)
def test_an_operator_opens_the_operational_pages(operator: Panel, url: str) -> None:
    assert operator.client.get(url).status_code == 200


def test_an_operator_does_not_see_admin_sections_in_the_menu(operator: Panel) -> None:
    page = operator.client.get("/admin/conversations").text

    assert "Conversaciones" in page
    for hidden in ("Usuarios", "Configuración general", "Plantillas de WhatsApp", "Métricas"):
        assert hidden not in page


def test_conversaciones_is_first_in_the_menu(logged_in: Panel) -> None:
    page = logged_in.client.get("/admin/conversations").text

    assert page.index("Conversaciones") < page.index("Edificios")


def test_an_operator_cannot_create_users(operator: Panel) -> None:
    response = operator.client.post(
        "/admin/users",
        data={
            "display_name": "Intrusa",
            "username": "intrusa",
            "role": "admin",
            "password": NEW_PASSWORD,
            "password2": NEW_PASSWORD,
        },
    )

    assert response.status_code == 403
    operator.session.expire_all()
    assert operator.session.scalar(select(PanelUser).where(PanelUser.username == "intrusa")) is None
    assert operator.admin_events("panel_user_created") == []


def test_an_operator_cannot_change_another_users_password(operator: Panel) -> None:
    other = make_user(operator.session, "lucia")
    before = other.password_hash

    response = operator.client.post(
        f"/admin/users/{other.id}",
        data={"change": "password", "password": NEW_PASSWORD, "password2": NEW_PASSWORD},
    )

    assert response.status_code == 403
    assert _user(operator, "lucia").password_hash == before


def test_a_table_admin_manages_users(panel: Panel) -> None:
    make_user(panel.session, TABLE_ADMIN, PanelRole.ADMIN)
    assert panel.login(TABLE_ADMIN, USER_PASSWORD).status_code == 302

    assert panel.client.get("/admin/users").status_code == 200


# --- The "Usuarios" page ----------------------------------------------------------------------


def test_admin_creates_a_user(logged_in: Panel) -> None:
    response = logged_in.client.post(
        "/admin/users",
        data={
            "display_name": "  Lucía   Inventada ",
            "username": "Lucia",
            "role": "operator",
            "password": NEW_PASSWORD,
            "password2": NEW_PASSWORD,
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    user = _user(logged_in, "lucia")
    assert user.display_name == "Lucía Inventada"
    assert user.role == PanelRole.OPERATOR and user.active
    assert verify_password(NEW_PASSWORD, user.password_hash)
    assert NEW_PASSWORD not in user.password_hash
    [event] = logged_in.admin_events("panel_user_created")
    assert event["admin_user"] == ADMIN and event["user_id"] == user.id
    assert "password" not in str(event) and user.password_hash not in str(event)
    # And she can log in.
    logged_in.client.get("/admin/logout")
    assert logged_in.login("lucia", NEW_PASSWORD).status_code == 302


@pytest.mark.parametrize(
    ("data", "problem"),
    [
        ({"username": ADMIN}, "reservado"),
        ({"username": "con espacio"}, "minúsculas"),
        ({"password": "corta", "password2": "corta"}, "al menos 12"),
        ({"password2": "distinta-de-la-otra"}, "no coinciden"),
        ({"display_name": " "}, "nombre"),
        ({"role": "duena"}, "rol"),
    ],
)
def test_create_user_problems(logged_in: Panel, data: dict[str, str], problem: str) -> None:
    form = {
        "display_name": "Lucía",
        "username": "lucia",
        "role": "operator",
        "password": NEW_PASSWORD,
        "password2": NEW_PASSWORD,
        **data,
    }

    response = logged_in.client.post("/admin/users", data=form)

    assert response.status_code == 400
    assert problem in response.text
    assert logged_in.admin_events("panel_user_created") == []


def test_duplicate_username_is_rejected(logged_in: Panel) -> None:
    make_user(logged_in.session, "lucia")

    response = logged_in.client.post(
        "/admin/users",
        data={
            "display_name": "Otra",
            "username": "LUCIA",
            "role": "operator",
            "password": NEW_PASSWORD,
            "password2": NEW_PASSWORD,
        },
    )

    assert response.status_code == 400
    assert "Ya existe" in response.text


def test_password_change_ends_her_sessions(panel: Panel) -> None:
    user = make_user(panel.session, OPERATOR)
    make_user(panel.session, TABLE_ADMIN, PanelRole.ADMIN)
    assert panel.login(OPERATOR, USER_PASSWORD).status_code == 302
    cookies = dict(panel.client.cookies)

    panel.client.cookies.clear()
    assert panel.login(TABLE_ADMIN, USER_PASSWORD).status_code == 302
    response = panel.client.post(
        f"/admin/users/{user.id}",
        data={"change": "password", "password": NEW_PASSWORD, "password2": NEW_PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert verify_password(NEW_PASSWORD, _user(panel, OPERATOR).password_hash)
    [event] = panel.admin_events("panel_user_password_changed")
    assert event["admin_user"] == TABLE_ADMIN and event["user_id"] == user.id

    # Her old session no longer works.
    panel.client.cookies.clear()
    panel.client.cookies.update(cookies)
    response = panel.client.get("/admin/conversations", follow_redirects=False)
    assert response.status_code == 302


def test_admin_deactivates_and_reactivates(logged_in: Panel) -> None:
    user = make_user(logged_in.session, OPERATOR)

    logged_in.client.post(f"/admin/users/{user.id}", data={"change": "deactivate"})
    assert not _user(logged_in, OPERATOR).active
    logged_in.client.post(f"/admin/users/{user.id}", data={"change": "reactivate"})
    assert _user(logged_in, OPERATOR).active

    actions = [e["action"] for e in logged_in.admin_events() if e["action"].startswith("panel_")]
    assert actions == ["panel_user_deactivated", "panel_user_reactivated"]


def test_an_admin_cannot_deactivate_or_demote_herself(panel: Panel) -> None:
    me = make_user(panel.session, TABLE_ADMIN, PanelRole.ADMIN)
    assert panel.login(TABLE_ADMIN, USER_PASSWORD).status_code == 302

    response = panel.client.post(f"/admin/users/{me.id}", data={"change": "deactivate"})
    assert response.status_code == 400
    response = panel.client.post(
        f"/admin/users/{me.id}", data={"change": "role", "role": "operator"}
    )
    assert response.status_code == 400

    user = _user(panel, TABLE_ADMIN)
    assert user.active and user.role == PanelRole.ADMIN


def test_role_change_applies_on_the_next_request(panel: Panel) -> None:
    user = make_user(panel.session, OPERATOR)
    assert panel.login(OPERATOR, USER_PASSWORD).status_code == 302
    assert panel.client.get("/admin/users").status_code == 403

    user.role = PanelRole.ADMIN
    panel.session.commit()

    assert panel.client.get("/admin/users").status_code == 200
