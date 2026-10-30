"""Вход, выход, текущий пользователь, профиль (пароль, фото)."""

from flask import Blueprint, current_app, jsonify, request, session
from flask_wtf.csrf import generate_csrf
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app import audit
from app.api.avatars import read_avatar_upload, store_avatar
from app.extensions import db, limiter
from app.ldap_auth import authenticate as ldap_authenticate
from app.ldap_auth import effective_mode, get_ldap_config
from app.models import User, UserAvatar, utcnow
from app.passwords import hash_password, password_problem, verify_password
from app.security import ApiError, current_user, login_required
from app.serializers import user_full
from app.sessions import revoke_user_sessions

bp = Blueprint("auth", __name__, url_prefix="/api")


class LoginRequest(BaseModel):
    login: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1, max_length=500)


class ChangePasswordRequest(BaseModel):
    currentPassword: str = Field(min_length=1, max_length=500)
    newPassword: str = Field(min_length=1, max_length=500)


def _login_rate_limit():
    return current_app.config.get("LOGIN_RATE_LIMIT", "10 per minute;50 per hour")


def _find_user(login_value):
    user = db.session.execute(select(User).where(User.login == login_value)).scalar_one_or_none()
    if user is None:
        matches = db.session.execute(
            select(User).where(func.lower(User.login) == login_value.lower())
        ).scalars().all()
        if len(matches) == 1:
            user = matches[0]
    if user is None and "@" in login_value:
        matches = db.session.execute(
            select(User).where(func.lower(User.email) == login_value.lower())
        ).scalars().all()
        if len(matches) == 1:
            user = matches[0]
    return user


def _local_ok(user, password):
    return user is not None and user.auth_source == "local" and verify_password(password, user.password_hash)


def _directory_user(result, config):
    """Сотрудник для успешного входа через каталог; None — учётную запись нельзя связать с каталогом."""
    user = _find_user(result.username)
    if user is not None and user.auth_source == "local" and user.role == "admin":
        # Локальный администратор не переводится на вход через каталог: иначе учётная запись каталога
        # с тем же логином или email получила бы права администратора, а пароль системы был бы стёрт
        return None
    if user is None:
        user = User(login=result.username, password_hash=None, name=result.name or result.username,
                    email=result.email or "", role=config.get("defaultRole") or "viewer",
                    auth_source="ldap", is_active=True)
        db.session.add(user)
        db.session.flush()
        audit.record("Пользователи", "create", f"user #{user.id} ({user.name})", "", "", "вход через LDAP", user=user)
        return user
    changed = False
    for attr, value in (("name", result.name), ("email", result.email)):
        if value and getattr(user, attr) != value:
            setattr(user, attr, value)
            changed = True
    if user.auth_source != "ldap":
        user.auth_source = "ldap"
        user.password_hash = None
        changed = True
    if changed:
        user.updated_at = utcnow()
    return user


def _authenticate(login_value, password):
    config = get_ldap_config()
    mode = effective_mode(config)
    local_user = _find_user(login_value)

    if mode == "local":
        return local_user if _local_ok(local_user, password) else None, None
    if mode == "ldap":
        # Локальный администратор сохраняет вход по паролю системы — иначе ошибка
        # в настройках каталога отрезала бы всех от системы.
        if local_user is not None and local_user.role == "admin" and _local_ok(local_user, password):
            return local_user, None
        result = ldap_authenticate(login_value, password, config)
        if not result.ok:
            return None, result.error
        return _directory_user(result, config), None
    # hybrid
    result = ldap_authenticate(login_value, password, config)
    if result.ok:
        user = _directory_user(result, config)
        if user is not None:
            return user, None
    if _local_ok(local_user, password):
        return local_user, None
    return None, None


@bp.get("/auth/csrf")
def csrf_token():
    return jsonify({"csrfToken": generate_csrf()})


@bp.get("/auth/config")
def auth_config():
    config = get_ldap_config()
    return jsonify({"mode": effective_mode(config), "ldapEnabled": bool(config.get("enabled"))})


def _login_key():
    """Попытки входа считаются по логину: подбор пароля к одной учётной записи. Весь офис за одним
    адресом (NAT, обратный прокси) не блокирует друг друга."""
    login_value = (request.get_json(silent=True) or {}).get("login")
    return "login:" + (login_value.strip().lower() if isinstance(login_value, str) else "")


def _login_ip_rate_limit():
    return current_app.config.get("LOGIN_IP_RATE_LIMIT", "300 per minute")


@bp.post("/auth/login")
@limiter.limit(_login_rate_limit, key_func=_login_key)
@limiter.limit(_login_ip_rate_limit)
def login():
    payload = LoginRequest.model_validate(request.get_json(silent=True) or {})
    user, directory_error = _authenticate(payload.login.strip(), payload.password)
    if user is None:
        if directory_error and effective_mode() == "ldap":
            raise ApiError(401, "invalid_credentials", "Ошибка входа через каталог: " + directory_error)
        raise ApiError(401, "invalid_credentials", "Неверный логин или пароль")
    if not user.is_active:
        raise ApiError(403, "account_disabled", "Учётная запись отключена — обратитесь к администратору")

    csrf_value = session.get("csrf_token")
    session.clear()
    session.regenerate()
    if csrf_value:
        session["csrf_token"] = csrf_value
    session["user_id"] = user.id
    user.last_login_at = utcnow()
    db.session.commit()
    return jsonify({"user": user_full(user)})


@bp.post("/auth/logout")
def logout():
    session.destroy()
    return jsonify({"ok": True})


@bp.get("/auth/me")
@login_required
def me():
    return jsonify({"user": user_full(current_user())})


@bp.put("/profile/password")
@login_required
def change_password():
    user = current_user()
    if user.auth_source == "ldap":
        raise ApiError(409, "directory_password", "Пароль учётной записи домена меняется в домене")
    payload = ChangePasswordRequest.model_validate(request.get_json(silent=True) or {})
    if not verify_password(payload.currentPassword, user.password_hash):
        raise ApiError(400, "wrong_password", "Неверный текущий пароль")
    problem = password_problem(payload.newPassword)
    if problem:
        raise ApiError(400, "weak_password", problem)
    user.password_hash = hash_password(payload.newPassword)
    audit.record("Пользователи", "update", f"user #{user.id} ({user.name})", "password", "***", "***")
    db.session.commit()
    # Остальные устройства сотрудника выходят из системы
    revoke_user_sessions(user.id, keep_token=session.token)
    return jsonify({"ok": True})


@bp.put("/profile/avatar")
@login_required
def upload_avatar():
    user = current_user()
    content_type, data = read_avatar_upload(current_app.config["AVATAR_MAX_BYTES"])
    store_avatar(user, content_type, data)
    audit.record("Пользователи", "update", f"user #{user.id} ({user.name})", "avatar", "", "фото обновлено")
    db.session.commit()
    return jsonify({"user": user_full(user)})


@bp.delete("/profile/avatar")
@login_required
def delete_avatar():
    user = current_user()
    avatar = db.session.get(UserAvatar, user.id)
    if avatar is not None:
        db.session.delete(avatar)
        user.avatar_version = 0
        audit.record("Пользователи", "update", f"user #{user.id} ({user.name})", "avatar", "", "фото удалено")
        db.session.commit()
    return jsonify({"user": user_full(user)})
