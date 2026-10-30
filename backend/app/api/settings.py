"""Настройки системы: вход через каталог (LDAP)."""

from typing import Literal

from flask import Blueprint, jsonify, request
from pydantic import BaseModel, Field

from app import audit
from app.extensions import db
from app.ldap_auth import DEFAULT_LDAP_CONFIG, get_ldap_config, test_connection
from app.models import AppSetting
from app.security import admin_required

bp = Blueprint("settings", __name__, url_prefix="/api/settings")

MASK = "••••••••"


class Attributes(BaseModel):
    username: str = Field(default="uid", max_length=100)
    name: str = Field(default="cn", max_length=100)
    email: str = Field(default="mail", max_length=100)


class LdapIn(BaseModel):
    enabled: bool = False
    serverUrl: str = Field(default="", max_length=500)
    baseDN: str = Field(default="", max_length=500)
    bindDN: str = Field(default="", max_length=500)
    bindPassword: str = Field(default="", max_length=500)
    searchFilter: str = Field(default="(uid={{username}})", max_length=500)
    userAttributes: Attributes = Attributes()
    authMode: Literal["local", "ldap", "hybrid"] = "local"
    defaultRole: Literal["viewer", "editor", "manager"] = "viewer"
    timeoutSeconds: int = Field(default=5, ge=1, le=60)


def _masked(config):
    return {**config, "bindPassword": MASK if config.get("bindPassword") else ""}


def _merge(payload):
    current = get_ldap_config()
    data = payload.model_dump()
    if data["bindPassword"] == MASK:
        data["bindPassword"] = current.get("bindPassword", "")
    if "{{username}}" not in data["searchFilter"]:
        from app.security import ApiError

        raise ApiError(400, "validation_error", "Фильтр поиска должен содержать {{username}}")
    return data


@bp.get("/ldap")
@admin_required
def get_ldap():
    return jsonify(_masked(get_ldap_config()))


@bp.put("/ldap")
@admin_required
def put_ldap():
    data = _merge(LdapIn.model_validate(request.get_json(silent=True) or {}))
    row = db.session.get(AppSetting, "ldap")
    old = get_ldap_config()
    if row is None:
        db.session.add(AppSetting(key="ldap", value=data))
    else:
        row.value = data
    changed = [k for k in DEFAULT_LDAP_CONFIG if old.get(k) != data.get(k)]
    if changed:
        audit.record("Настройки", "update", "LDAP", ", ".join(changed), "", "изменено")
    db.session.commit()
    return jsonify(_masked(data))


@bp.post("/ldap/test")
@admin_required
def test_ldap():
    payload = request.get_json(silent=True)
    config = _merge(LdapIn.model_validate(payload)) if payload else get_ldap_config()
    return jsonify(test_connection(config))
