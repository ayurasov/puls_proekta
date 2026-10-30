"""Сотрудники: справочник для всех, управление — администратору."""

from typing import Literal, Optional

from flask import Blueprint, current_app, jsonify, request, session
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from app import audit
from app.api.avatars import read_avatar_upload, store_avatar
from app.extensions import db
from app.models import Project, User, UserAvatar, utcnow
from app.passwords import hash_password, password_problem
from app.security import ApiError, admin_required, current_user, login_required
from app.serializers import user_full, user_public
from app.sessions import revoke_user_sessions

bp = Blueprint("users", __name__, url_prefix="/api/users")

ROLE_LABELS = {"admin": "Администратор", "manager": "Менеджер", "editor": "Редактор", "viewer": "Наблюдатель"}
Role = Literal["admin", "manager", "editor", "viewer"]


def _clean(value):
    # Неразрывные и концевые пробелы из 1.0.x («Руководитель проектов\xa0») не нужны
    return " ".join((value or "").replace("\xa0", " ").split())


class UserCreate(BaseModel):
    login: str = Field(min_length=1, max_length=150)
    name: str = Field(min_length=1, max_length=255)
    password: str = Field(default="", max_length=200)
    email: str = Field(default="", max_length=255)
    position: str = Field(default="", max_length=500)
    role: Role = "viewer"
    authSource: Literal["local", "ldap"] = "local"

    @field_validator("login", "name", "email", "position")
    @classmethod
    def _strip(cls, v):
        return _clean(v)


class UserUpdate(BaseModel):
    login: Optional[str] = Field(default=None, min_length=1, max_length=150)
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    password: Optional[str] = Field(default=None, max_length=200)
    email: Optional[str] = Field(default=None, max_length=255)
    position: Optional[str] = Field(default=None, max_length=500)
    role: Optional[Role] = None
    isActive: Optional[bool] = None
    authSource: Optional[Literal["local", "ldap"]] = None
    # Структура компании (ADR-0008); пустая строка / 0 — снять
    departmentId: Optional[str] = Field(default=None, max_length=36)
    managerId: Optional[int] = None
    reportsToNone: Optional[bool] = None

    @field_validator("login", "name", "email", "position")
    @classmethod
    def _strip(cls, v):
        return None if v is None else _clean(v)


def _login_taken(login, exclude_id=None):
    stmt = select(User.id).where(func.lower(User.login) == login.lower())
    if exclude_id is not None:
        stmt = stmt.where(User.id != exclude_id)
    return db.session.execute(stmt).first() is not None


def _active_admins_except(user_id):
    return db.session.execute(
        select(func.count()).select_from(User).where(User.role == "admin", User.is_active.is_(True), User.id != user_id)
    ).scalar_one()


def _get(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        raise ApiError(404, "not_found", "Сотрудник не найден")
    return user


@bp.get("/directory")
@login_required
def directory():
    """Активные сотрудники — для привязки исполнителей и отображения авторов."""
    users = db.session.execute(select(User).where(User.is_active.is_(True)).order_by(User.name)).scalars().all()
    return jsonify([user_public(u) for u in users])


@bp.get("")
@admin_required
def list_users():
    users = db.session.execute(select(User).order_by(User.name)).scalars().all()
    created = dict(db.session.execute(
        select(Project.created_by_id, func.count()).where(Project.deleted_at.is_(None)).group_by(Project.created_by_id)
    ).all())
    result = []
    for user in users:
        item = user_full(user)
        item["projectsCreated"] = created.get(user.id, 0)
        result.append(item)
    return jsonify(result)


@bp.post("")
@admin_required
def create_user():
    payload = UserCreate.model_validate(request.get_json(silent=True) or {})
    if _login_taken(payload.login):
        raise ApiError(409, "login_taken", "Логин уже занят")
    password_hash = None
    if payload.authSource == "local":
        problem = password_problem(payload.password)
        if problem:
            raise ApiError(400, "weak_password", problem)
        password_hash = hash_password(payload.password)
    user = User(login=payload.login, name=payload.name, email=payload.email, position=payload.position,
                role=payload.role, auth_source=payload.authSource, password_hash=password_hash, is_active=True)
    db.session.add(user)
    db.session.flush()
    audit.record("Пользователи", "create", f"user #{user.id} ({user.name})", "", "", user.name)
    db.session.commit()
    return jsonify(user_full(user)), 201


@bp.patch("/<int:user_id>")
@admin_required
def update_user(user_id):
    me = current_user()
    payload = UserUpdate.model_validate(request.get_json(silent=True) or {})
    # Подразделение, руководитель и включение меняют цепочки подчинения — по одной правке структуры за раз
    org_change = bool({"departmentId", "managerId", "reportsToNone", "isActive"} & payload.model_fields_set)
    if org_change:
        from app.services.org import lock_org

        lock_org()
    user = _get(user_id)
    entity = f"user #{user.id} ({user.name})"
    revoke = False

    if payload.isActive is False and user.id == me.id:
        raise ApiError(400, "self_deactivate", "Нельзя отключить свою учётную запись")
    if payload.role is not None and payload.role != "admin" and user.id == me.id:
        raise ApiError(400, "self_demote", "Нельзя снять с себя роль администратора")
    losing_admin = user.role == "admin" and user.is_active and (
        (payload.role is not None and payload.role != "admin") or payload.isActive is False
    )
    if losing_admin and _active_admins_except(user.id) == 0:
        raise ApiError(400, "last_admin", "В системе должен остаться хотя бы один активный администратор")
    if payload.login is not None and payload.login != user.login and _login_taken(payload.login, user.id):
        raise ApiError(409, "login_taken", "Логин уже занят")

    changes = []
    for field, attr in (("login", "login"), ("name", "name"), ("email", "email"), ("position", "position"),
                        ("role", "role"), ("isActive", "is_active"), ("authSource", "auth_source")):
        value = getattr(payload, field)
        if value is None:
            continue
        old = getattr(user, attr)
        if old != value:
            setattr(user, attr, value)
            changes.append((attr, old, value))
            if attr == "is_active" and value is False:
                revoke = True
            if attr == "auth_source" and value == "ldap":
                user.password_hash = None

    changes += _apply_org_fields(user, payload)

    if payload.password:
        if user.auth_source == "ldap":
            raise ApiError(400, "directory_password", "У учётной записи домена нет пароля системы")
        problem = password_problem(payload.password)
        if problem:
            raise ApiError(400, "weak_password", problem)
        user.password_hash = hash_password(payload.password)
        changes.append(("password", "***", "***"))
        revoke = True

    for attr, old, new in changes:
        labels = {"role": ROLE_LABELS}
        if attr in labels:
            old, new = labels[attr].get(old, old), labels[attr].get(new, new)
        if attr == "is_active":
            old, new = ("активен" if old else "отключён"), ("активен" if new else "отключён")
        audit.record("Пользователи", "update", entity, attr, old, new)
    if changes:
        user.updated_at = utcnow()
    if org_change:
        from app.services.org import assert_consistent

        assert_consistent()
    db.session.commit()
    if revoke:
        revoke_user_sessions(user.id, keep_token=session.token if user.id == me.id else None)
    return jsonify(user_full(user))


def _apply_org_fields(user, payload):
    """Подразделение, явный руководитель и «первое лицо» (ADR-0008)."""
    from app.models import Department
    from app.services.org import OrgSnapshot, would_cycle

    changes = []
    fields = payload.model_fields_set
    snap = None
    if "departmentId" in fields:
        dep_id = payload.departmentId or None
        if dep_id and db.session.get(Department, dep_id) is None:
            raise ApiError(400, "validation_error", "Подразделение не найдено")
        if dep_id != user.department_id:
            snap = OrgSnapshot()
            changes.append(("department", " / ".join(snap.path(user.department_id)), " / ".join(snap.path(dep_id))))
            user.department_id = dep_id
    if "managerId" in fields:
        manager_id = payload.managerId or None
        if manager_id is not None:
            manager = db.session.get(User, manager_id)
            if manager is None or not manager.is_active:
                raise ApiError(400, "validation_error", "Руководитель не найден или отключён")
            if would_cycle(user.id, manager_id):
                raise ApiError(409, "org_cycle", "Цикл подчинения: руководитель подчиняется этому сотруднику")
        if manager_id != user.manager_id:
            old = db.session.get(User, user.manager_id).name if user.manager_id else "по структуре"
            new = db.session.get(User, manager_id).name if manager_id else "по структуре"
            changes.append(("manager", old, new))
            user.manager_id = manager_id
            if manager_id:
                user.reports_to_none = False
    if payload.reportsToNone is not None and payload.reportsToNone != bool(user.reports_to_none):
        changes.append(("reportsToNone", "нет" if payload.reportsToNone else "да", "да" if payload.reportsToNone else "нет"))
        user.reports_to_none = payload.reportsToNone
        if payload.reportsToNone:
            user.manager_id = None
    return changes


@bp.delete("/<int:user_id>")
@admin_required
def delete_user(user_id):
    me = current_user()
    user = _get(user_id)
    if user.id == me.id:
        raise ApiError(400, "self_delete", "Нельзя удалить свою учётную запись")
    if user.role == "admin" and user.is_active and _active_admins_except(user.id) == 0:
        raise ApiError(400, "last_admin", "В системе должен остаться хотя бы один активный администратор")
    audit.record("Пользователи", "delete", f"user #{user.id} ({user.name})", "", user.name, "")
    revoke_user_sessions(user.id)
    db.session.delete(user)
    db.session.commit()
    return jsonify({"ok": True})


@bp.put("/<int:user_id>/avatar")
@admin_required
def admin_upload_avatar(user_id):
    user = _get(user_id)
    content_type, data = read_avatar_upload(current_app.config["AVATAR_MAX_BYTES"])
    store_avatar(user, content_type, data)
    audit.record("Пользователи", "update", f"user #{user.id} ({user.name})", "avatar", "", "фото обновлено")
    db.session.commit()
    return jsonify(user_full(user))


@bp.delete("/<int:user_id>/avatar")
@admin_required
def admin_delete_avatar(user_id):
    user = _get(user_id)
    avatar = db.session.get(UserAvatar, user.id)
    if avatar is not None:
        db.session.delete(avatar)
        user.avatar_version = 0
        audit.record("Пользователи", "update", f"user #{user.id} ({user.name})", "avatar", "", "фото удалено")
        db.session.commit()
    return jsonify(user_full(user))
