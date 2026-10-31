"""«По Делу»: ответная интеграция — ключи обмена и задачи «По Делу», связанные с проектами (ADR-0010).

Администратор («Интеграции → «По Делу» → Ссылки из задач»):

* GET|PUT|DELETE /api/settings/podelu/links — адрес «По Делу», ключ «По Делу» (вводится, не возвращается),
  «Не проверять TLS», показ задач «По Делу» в «Пульсе» (включается только после успешной проверки);
* POST /api/settings/podelu/links/test — проверка связи (ping «По Делу» с ключом); для сохранённых адреса и ключа
  запоминается время и версия «По Делу»;
* POST|DELETE /api/settings/podelu/links/key — выпустить (показывается один раз) или отозвать ключ «Пульса».

Сотрудники с доступом к проекту:

* GET /api/projects/<id>/podelu-tasks?item=<ключ строки> — задачи «По Делу», которые ссылаются на проект (строку);
* GET /api/podelu/links/summary?projects=1,2 — сколько задач «По Делу» у проектов (для карточек на главной).
"""

from typing import Optional

from flask import jsonify, request, session
from pydantic import BaseModel, Field

from app import audit
from app.api.podelu import bp
from app.extensions import db, limiter
from app.models import Project
from app.podelu_client import PodeluError, check_base_url
from app.security import ApiError, admin_required, current_user, login_required, project_levels, require_project
from app.services import podelu as podelu_service
from app.services import podelu_links as links


def _user_key():
    return f"user:{session.get('user_id')}"


class LinksIn(BaseModel):
    baseUrl: str = Field(default="", max_length=500)
    key: str = Field(default="", max_length=300)
    acceptInvalidCerts: bool = False
    enabled: bool = False


def _form(payload):
    data = payload.model_dump()
    try:
        data["baseUrl"] = check_base_url(data["baseUrl"]) if data["baseUrl"].strip() else ""
        data["key"] = links.check_key(data["key"]) if data["key"].strip() else ""
    except PodeluError as exc:
        raise ApiError(400, "validation_error", str(exc)) from None
    return data


def _suggested_url():
    conn = podelu_service.get_connection()
    return conn["baseUrl"] if conn else ""


@bp.get("/settings/podelu/links")
@admin_required
def get_links():
    return jsonify(links.admin_json(suggested_url=_suggested_url()))


@bp.put("/settings/podelu/links")
@admin_required
def put_links():
    data = _form(LinksIn.model_validate(request.get_json(silent=True) or {}))
    settings = links.get_settings()
    old = dict(settings)
    if not data["baseUrl"]:
        raise ApiError(400, "validation_error", "Укажите адрес «По Делу»")
    if not data["key"]:
        if not settings["keyEncrypted"]:
            if data["enabled"]:
                raise ApiError(400, "validation_error", "Введите ключ «По Делу»")
        elif settings["baseUrl"] != data["baseUrl"]:
            # сохранённый ключ уходит только на тот адрес, для которого он был введён
            raise ApiError(400, "validation_error", "Адрес «По Делу» изменён — введите ключ заново")
        elif data["acceptInvalidCerts"] and not settings["acceptInvalidCerts"]:
            raise ApiError(400, "validation_error", "Без проверки сертификата введите ключ заново")
    if data["key"]:
        settings["keyEncrypted"] = links.encrypt_key(data["key"])
        key = data["key"]
    else:
        key = links.decrypt_key(settings["keyEncrypted"]) if settings["keyEncrypted"] else ""
    settings["baseUrl"] = data["baseUrl"]
    settings["acceptInvalidCerts"] = data["acceptInvalidCerts"]
    # Проверка действительна, пока не менялись адрес и ключ
    if settings["checkedFingerprint"] != links.fingerprint(data["baseUrl"], key):
        settings.update({"checkedAt": None, "checkedVersion": "", "checkedFingerprint": ""})
    if data["enabled"] and not settings["checkedAt"]:
        raise ApiError(409, "check_required", "Сначала проверьте связь с «По Делу» — показ задач включается после "
                       "успешной проверки")
    settings["enabled"] = data["enabled"]
    links.save_settings(settings)
    changed = [k for k in ("baseUrl", "acceptInvalidCerts", "enabled") if old.get(k) != settings.get(k)]
    if data["key"]:
        changed.append("key")
    if changed:
        audit.record("Настройки", "update", "По Делу: ссылки из задач", ", ".join(changed)[:100],
                     old.get("baseUrl") or "", settings["baseUrl"])
    db.session.commit()
    return jsonify(links.admin_json(suggested_url=_suggested_url()))


@bp.delete("/settings/podelu/links")
@admin_required
def delete_links():
    settings = links.get_settings()
    if settings["baseUrl"] or settings["keyEncrypted"] or settings["inbound"]:
        links.delete_settings()
        audit.record("Настройки", "delete", "По Делу: ссылки из задач", "", settings["baseUrl"] or "", "")
        db.session.commit()
    return jsonify({"ok": True})


@bp.post("/settings/podelu/links/test")
@admin_required
@limiter.limit("20 per minute", key_func=_user_key)
def test_links():
    """Проверка связи: «По Делу» принимает ключ и отвечает своей версией. Для сохранённых адреса и ключа время
    проверки запоминается — после неё можно включить показ задач."""
    payload = request.get_json(silent=True)
    override = _form(LinksIn.model_validate(payload)) if payload and payload.get("baseUrl") else None
    try:
        client, base = links.make_client(override=override)
    except PodeluError as exc:
        raise ApiError(400, "validation_error", str(exc)) from None
    db.session.rollback()  # к «По Делу» — без открытой транзакции базы
    try:
        info = client.ping()
    except PodeluError as exc:
        raise ApiError(502, "podelu_error", str(exc)) from None
    settings = links.get_settings()
    saved_key = links.decrypt_key(settings["keyEncrypted"]) if settings["keyEncrypted"] else ""
    remembered = bool(settings["baseUrl"] == base and saved_key == client.key and
                      settings["acceptInvalidCerts"] == bool(client.session.verify is False))
    if remembered:
        settings.update({"checkedAt": links.now_iso(), "checkedVersion": info["version"],
                         "checkedFingerprint": links.fingerprint(base, client.key)})
        links.save_settings(settings)
        db.session.commit()
    warnings = []
    if not info["linksEnabled"]:
        warnings.append("В «По Делу» ссылки на проекты в задачах пока выключены — включите их там: Настройки → "
                        "Интеграции → «Пульс проекта»")
    if not settings.get("inbound"):
        warnings.append("Ключ «Пульса» для «По Делу» ещё не выпущен — без него «По Делу» не сможет искать проекты")
    return jsonify({"ok": True, **info, "remembered": remembered, "warnings": warnings,
                    "settings": links.admin_json(suggested_url=_suggested_url())})


@bp.post("/settings/podelu/links/key")
@admin_required
@limiter.limit("10 per minute", key_func=_user_key)
def issue_key():
    had = bool(links.get_settings().get("inbound"))
    key, meta = links.issue_inbound_key()
    audit.record("Настройки", "update" if had else "create", "По Делу: ключ «Пульса»", "key", "", meta["prefix"] + "…")
    db.session.commit()
    return jsonify({"key": key, "prefix": meta["prefix"], "createdAt": meta["createdAt"]})


@bp.delete("/settings/podelu/links/key")
@admin_required
def revoke_key():
    settings = links.get_settings()
    if settings.get("inbound"):
        links.revoke_inbound_key()
        audit.record("Настройки", "delete", "По Делу: ключ «Пульса»", "key", settings["inbound"]["prefix"] + "…", "")
        db.session.commit()
    return jsonify({"ok": True})


# ---------- сотрудники ----------

@bp.get("/podelu/links")
@login_required
def links_status():
    settings = links.get_settings()
    return jsonify({"enabled": links.ready(settings), "baseUrl": settings["baseUrl"] if links.ready(settings) else None})


def _client_or_none():
    settings = links.get_settings()
    if not links.ready(settings):
        return None
    try:
        return links.make_client()[0]
    except PodeluError:
        return None


@bp.get("/projects/<int:project_id>/podelu-tasks")
@login_required
@limiter.limit("60 per minute", key_func=_user_key)
def project_tasks(project_id):
    """Задачи «По Делу», которые ссылаются на проект (с item — на строку плана): {enabled, tasks, error}."""
    require_project(project_id)
    item = (request.args.get("item") or "").strip()[:64] or None
    settings = links.get_settings()
    if not links.ready(settings):
        return jsonify({"enabled": False, "tasks": [], "error": None})
    db.session.rollback()
    try:
        client = links.make_client()[0]
        tasks, truncated = client.project_tasks(project_id, item=item)
    except PodeluError as exc:
        message = str(exc) if current_user().role == "admin" else \
            "«По Делу» сейчас не отвечает — задачи появятся позже"
        return jsonify({"enabled": True, "tasks": [], "truncated": False, "error": message})
    return jsonify({"enabled": True, "tasks": tasks, "truncated": truncated, "error": None,
                    "baseUrl": settings["baseUrl"]})


class SummaryArgs(BaseModel):
    projects: Optional[str] = Field(default="", max_length=4000)


@bp.get("/podelu/links/summary")
@login_required
@limiter.limit("30 per minute", key_func=_user_key)
def summary():
    """Число задач «По Делу» у доступных сотруднику проектов: {counts: {id: {total, open}}}."""
    raw = SummaryArgs.model_validate(request.args.to_dict()).projects or ""
    ids = [int(x) for x in raw.split(",")[:500] if x.strip().isdigit()]
    if not ids:
        return jsonify({"enabled": links.ready(), "counts": {}})
    user = current_user()
    projects = db.session.query(Project).filter(Project.id.in_(ids), Project.deleted_at.is_(None)).all()
    allowed = sorted(project_levels(user, projects))
    if not links.ready():
        return jsonify({"enabled": False, "counts": {}})
    db.session.rollback()
    try:
        counts = links.make_client()[0].summary(allowed)
    except PodeluError:
        return jsonify({"enabled": True, "counts": {}, "error": "«По Делу» не отвечает"})
    return jsonify({"enabled": True, "counts": {str(k): v for k, v in counts.items() if k in allowed}})
