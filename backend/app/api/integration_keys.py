"""Ключи интеграций — администратор (ADR-0011): «Администрирование → Интеграции → Ключи доступа».

* GET    /api/settings/integration-keys — ключи (действующие и отозванные), каталог прав и систем;
* POST   /api/settings/integration-keys — выпустить {name, app, scopes} → {key, item}: ключ показывается один раз;
* POST   /api/settings/integration-keys/<id>/reissue — новый ключ той же записи, прежний перестаёт действовать;
* PATCH  /api/settings/integration-keys/<id> — {name?, scopes?};
* DELETE /api/settings/integration-keys/<id> — отозвать (запись остаётся в списке).

Каждое действие — в реестре изменений, раздел «Интеграции» (без самого ключа).
"""

from typing import Literal, Optional

from flask import Blueprint, jsonify, request, session
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.extensions import db, limiter
from app.models import IntegrationKey
from app.security import ApiError, admin_required, current_user
from app.services import integration_keys as keys

bp = Blueprint("integration_keys", __name__, url_prefix="/api/settings/integration-keys")


def _user_key():
    return f"user:{session.get('user_id')}"


class IssueIn(BaseModel):
    name: str = Field(max_length=200)
    app: Literal["podelu", "dashboards", "other"] = "other"
    scopes: list[str] = Field(default_factory=list, max_length=10)


class UpdateIn(BaseModel):
    name: Optional[str] = Field(default=None, max_length=200)
    scopes: Optional[list[str]] = Field(default=None, max_length=10)


def _row(key_id):
    row = db.session.get(IntegrationKey, key_id)
    if row is None:
        raise ApiError(404, "not_found", "Ключ не найден")
    return row


def _payload():
    rows = db.session.execute(select(IntegrationKey).order_by(
        IntegrationKey.revoked_at.is_not(None), IntegrationKey.issued_at.desc(), IntegrationKey.id.desc())).scalars().all()
    return {
        "keys": keys.serialize_all(rows),
        "scopes": [{"value": k, "label": v} for k, v in keys.SCOPES.items()],
        "apps": [{"value": k, "label": v, "scopes": keys.DEFAULT_SCOPES[k]} for k, v in keys.APPS.items()],
    }


def _failed(exc):
    return ApiError(400, "validation_error", str(exc))


@bp.get("")
@admin_required
def list_keys():
    return jsonify(_payload())


@bp.post("")
@admin_required
@limiter.limit("20 per minute", key_func=_user_key)
def issue_key():
    data = IssueIn.model_validate(request.get_json(silent=True) or {})
    try:
        key, row = keys.issue(data.name, data.app, data.scopes, current_user())
    except keys.IntegrationKeyError as exc:
        raise _failed(exc) from None
    db.session.commit()
    return jsonify({"key": key, "item": keys.serialize_all([row])[0]}), 201


@bp.post("/<int:key_id>/reissue")
@admin_required
@limiter.limit("20 per minute", key_func=_user_key)
def reissue_key(key_id):
    row = _row(key_id)
    try:
        key = keys.reissue(row, current_user())
    except keys.IntegrationKeyError as exc:
        raise _failed(exc) from None
    db.session.commit()
    return jsonify({"key": key, "item": keys.serialize_all([row])[0]})


@bp.patch("/<int:key_id>")
@admin_required
def update_key(key_id):
    row = _row(key_id)
    data = UpdateIn.model_validate(request.get_json(silent=True) or {})
    try:
        keys.update(row, current_user(), name=data.name, scopes=data.scopes)
    except keys.IntegrationKeyError as exc:
        raise _failed(exc) from None
    db.session.commit()
    return jsonify({"item": keys.serialize_all([row])[0]})


@bp.delete("/<int:key_id>")
@admin_required
def revoke_key(key_id):
    row = _row(key_id)
    keys.revoke(row, current_user())
    db.session.commit()
    return jsonify({"item": keys.serialize_all([row])[0]})
