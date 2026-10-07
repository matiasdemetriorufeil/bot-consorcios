"""Panel page "Usuarios" (admins only): one user per employee, with role admin or operator.

Users are never deleted (the audit log and the conversations keep their names): they are
deactivated. Changing the password or deactivating raises session_version, which ends the
user's open sessions (app.admin.auth). The .env user is the rescue admin: it is not listed
here and its name cannot be taken. Audited without passwords or hashes.
"""

from typing import Any, ClassVar

from sqladmin import BaseView, expose
from sqladmin.flash import Flash
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import FormData
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from app.admin import formatting, labels
from app.admin.audit import log_admin_action
from app.admin.auth import (
    MIN_PASSWORD_LENGTH,
    AdminOnly,
    admin_user,
    current_user_id,
    find_user,
    hash_password,
)
from app.config import Settings
from app.db.models import PanelRole, PanelUser

ROLE_LABELS = labels.PANEL_ROLE
USERNAME_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789._-")


class UserProblem(ValueError):
    """What is wrong in the form, in Spanish (shown as is)."""


def _password(form: FormData) -> str:
    password = str(form.get("password", ""))
    if len(password) < MIN_PASSWORD_LENGTH:
        raise UserProblem(
            f"La contraseña tiene que tener al menos {MIN_PASSWORD_LENGTH} caracteres."
        )
    if password != str(form.get("password2", "")):
        raise UserProblem("Las contraseñas no coinciden.")
    return password


def _role(form: FormData) -> PanelRole:
    try:
        return PanelRole(str(form.get("role", "")))
    except ValueError as exc:
        raise UserProblem("Elegí el rol.") from exc


def create_user(session: Session, form: FormData, actor: str, env_username: str) -> PanelUser:
    username = str(form.get("username", "")).strip().lower()
    display_name = " ".join(str(form.get("display_name", "")).split())
    if not display_name:
        raise UserProblem("Escribí el nombre (es el que se ve en las conversaciones).")
    if not username or not set(username) <= USERNAME_CHARS or len(username) > 100:
        raise UserProblem("El usuario va en minúsculas, sin espacios (letras, números, . _ -).")
    if env_username and username == env_username.lower():
        raise UserProblem("Ese usuario está reservado para el admin de rescate.")
    if find_user(session, username) is not None:
        raise UserProblem("Ya existe un usuario con ese nombre.")
    user = PanelUser(
        username=username,
        display_name=display_name[:100],
        password_hash=hash_password(_password(form)),
        role=_role(form),
        active=True,
    )
    session.add(user)
    session.flush()
    log_admin_action(session, actor, "panel_user_created", user_id=user.id, role=user.role.value)
    session.commit()
    return user


def update_user(
    session: Session, user: PanelUser, form: FormData, actor: str, actor_id: int | None
) -> str:
    """One change per submit (the form's "change" field). Returns what to show."""
    change = str(form.get("change", ""))
    myself = actor_id is not None and actor_id == user.id
    if change == "password":
        user.password_hash = hash_password(_password(form))
        user.session_version += 1
        log_admin_action(session, actor, "panel_user_password_changed", user_id=user.id)
        message = "Contraseña cambiada: sus sesiones abiertas se cerraron."
    elif change == "role":
        role = _role(form)
        if myself and role != PanelRole.ADMIN:
            raise UserProblem("No podés quitarte tu propio rol de admin.")
        if role == user.role:
            return "No había cambios."
        user.role = role
        log_admin_action(
            session, actor, "panel_user_role_changed", user_id=user.id, role=role.value
        )
        message = f"Rol cambiado a {ROLE_LABELS[role]}."
    elif change == "name":
        name = " ".join(str(form.get("display_name", "")).split())
        if not name:
            raise UserProblem("Escribí el nombre.")
        user.display_name = name[:100]
        log_admin_action(session, actor, "panel_user_renamed", user_id=user.id)
        message = "Nombre cambiado."
    elif change == "deactivate":
        if myself:
            raise UserProblem("No podés desactivar tu propio usuario.")
        if not user.active:
            return "Ya estaba desactivado."
        user.active = False
        user.session_version += 1
        log_admin_action(session, actor, "panel_user_deactivated", user_id=user.id)
        message = "Usuario desactivado: ya no puede entrar."
    elif change == "reactivate":
        if user.active:
            return "Ya estaba activo."
        user.active = True
        log_admin_action(session, actor, "panel_user_reactivated", user_id=user.id)
        message = "Usuario reactivado."
    else:
        raise UserProblem("Cambio desconocido.")
    session.commit()
    return message


class UsersView(AdminOnly, BaseView):
    name = "Usuarios"
    icon = "fa-solid fa-users"
    session_maker: ClassVar[Any] = None
    timezone: ClassVar[str] = Settings.model_fields["timezone"].default
    env_username: ClassVar[str] = ""

    async def _render(
        self, request: Request, name: str, title: str, status_code: int = 200, **context: Any
    ) -> Response:
        return await self.templates.TemplateResponse(
            request,
            name,
            {"title": title, "roles": ROLE_LABELS, **context},
            status_code=status_code,
        )

    def _local(self, value: Any) -> str:
        return formatting.when(value, timezone=self.timezone) if value else ""

    @expose("/users", methods=["GET", "POST"], identity="users")
    async def users_page(self, request: Request) -> Response:
        error = None
        form: FormData | None = None
        with self.session_maker() as session:
            if request.method == "POST":
                form = await request.form()
                try:
                    user = create_user(session, form, admin_user(request), self.env_username)
                except UserProblem as exc:
                    session.rollback()
                    error = str(exc)
                else:
                    Flash.success(request, f"Usuario {user.username} creado.")
                    return RedirectResponse(request.url_for("admin:view-users"), status_code=302)
            users = list(
                session.scalars(
                    select(PanelUser).order_by(PanelUser.active.desc(), PanelUser.display_name)
                )
            )
            rows = [(u, self._local(u.last_login_at)) for u in users]
            return await self._render(
                request,
                "users.html",
                "Usuarios del panel",
                status_code=400 if error else 200,
                rows=rows,
                error=error,
                form=form,
                env_username=self.env_username,
                min_length=MIN_PASSWORD_LENGTH,
            )

    @expose("/users/{user_id:int}", methods=["GET", "POST"], identity="user-edit")
    async def user_page(self, request: Request) -> Response:
        user_id = request.path_params["user_id"]
        error = None
        with self.session_maker() as session:
            user = session.get(PanelUser, user_id)
            if user is None:
                return Response("Usuario inexistente", status_code=404)
            if request.method == "POST":
                try:
                    message = update_user(
                        session,
                        user,
                        await request.form(),
                        admin_user(request),
                        current_user_id(request),
                    )
                except UserProblem as exc:
                    session.rollback()
                    error = str(exc)
                else:
                    Flash.success(request, message)
                    return RedirectResponse(
                        request.url_for("admin:view-user-edit", user_id=user_id), status_code=302
                    )
            return await self._render(
                request,
                "user_edit.html",
                f"Usuario {user.username}",
                status_code=400 if error else 200,
                user=user,
                last_login=self._local(user.last_login_at),
                myself=current_user_id(request) == user.id,
                error=error,
                min_length=MIN_PASSWORD_LENGTH,
            )
