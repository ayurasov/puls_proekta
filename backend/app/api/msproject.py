"""MS Project (ADR-0012): загрузка файла MS Project XML — новым проектом, новым планом, заменой плана или обновлением
плана, который ведётся в MS Project, — и выгрузка плана в MS Project XML.

Загрузка в два шага, как «Импорт и экспорт» структуры: `preview` — план загрузки без записи, `apply` — то же с
отпечатком файла и параметров. Файл хранит браузер и присылает оба раза; на сервере он не сохраняется.
"""

import json
import re
from urllib.parse import quote

from flask import Blueprint, Response, jsonify, request, session
from sqlalchemy import func, select

from app import audit
from app.api.plans import _audit_plan, _plan_with_access
from app.extensions import db, limiter
from app.models import CalendarDay, Direction, ItemComment, Project, User
from app.security import ApiError, current_user, login_required, require_project
from app.services import msproject_import as importer
from app.services.msproject import write_mspdi
from app.services.plan_doc import snapshot_document
from app.services.plan_ops import lock_plan
from app.services.plans import create_plan, unique_plan_title

bp = Blueprint("msproject", __name__, url_prefix="/api")


def _user_key():
    return f"user:{session.get('user_id')}"


def _form():
    """Файл и параметры из multipart-запроса."""
    upload = request.files.get("file")
    if upload is None:
        raise ApiError(400, "validation_error", "Выберите файл MS Project XML")
    data = upload.read()
    if not data:
        raise ApiError(400, "validation_error", "Файл пустой")
    try:
        raw = json.loads(request.form.get("options") or "{}")
    except ValueError:
        raise ApiError(400, "validation_error", "Неверные параметры загрузки") from None
    target = request.form.get("target") or "project"
    if target not in importer.TARGETS:
        raise ApiError(400, "validation_error", "Неизвестное назначение загрузки")
    return data, raw, target, (upload.filename or "")[:255]


def _int(name):
    value = request.form.get(name)
    if value in (None, ""):
        return None
    try:
        return int(value)
    except ValueError:
        raise ApiError(400, "validation_error", f"Неверное значение {name}") from None


def _check_target(target, user):
    """Права и объект назначения: (проект, план) — для нового проекта оба None."""
    if target == "project":
        if user.role not in ("admin", "manager"):
            raise ApiError(403, "forbidden", "Создавать проекты могут администратор и менеджер")
        return None, None
    if target == "plan":
        project, _level = require_project(_int("projectId") or 0, "edit")
        return project, None
    # План в архиве не меняется ни правками, ни загрузкой
    plan, project, _level = _plan_with_access(_int("planId") or 0, "edit", allow_archived=False)
    return project, plan


@bp.post("/import/msproject/preview")
@login_required
@limiter.limit("30 per minute", key_func=_user_key)
def preview():
    user = current_user()
    data, raw, target, file_name = _form()
    _project, plan = _check_target(target, user)
    options = importer.clean_options(raw, target, plan)
    return jsonify(importer.preview(data, options, target, plan=plan, file_name=file_name, viewer=user))


@bp.post("/import/msproject/apply")
@login_required
@limiter.limit("10 per minute", key_func=_user_key)
def apply():
    user = current_user()
    data, raw, target, file_name = _form()
    project, plan = _check_target(target, user)
    options = importer.clean_options(raw, target, plan)
    if request.form.get("fingerprint") != importer.fingerprint(data, options):
        raise ApiError(409, "preview_outdated", "Файл или параметры изменились после проверки — проверьте ещё раз")
    if plan is not None:
        # Как обычная правка: план заблокирован до конца транзакции, ревизия — перечитана после блокировки
        plan = lock_plan(plan.id)
    doc = importer.read_document(data, options, file_name, viewer=user)
    title = " ".join((request.form.get("title") or doc["project"].get("title") or doc["project"].get("name")
                      or "Проект из MS Project").split())[:500]

    if target == "project":
        direction_id = _int("directionId")
        if direction_id is not None and db.session.get(Direction, direction_id) is None:
            raise ApiError(400, "validation_error", "Направление не найдено")
        project = Project(title=title, subtitle="", direction_id=direction_id, created_by_id=user.id,
                          position=_next_project_position(direction_id))
        db.session.add(project)
        db.session.flush()
        plan = create_plan(project, user, "Основной план", settings=importer.settings_for(doc))
        importer.fill(plan, user, doc, f"План создан импортом из MS Project: {file_name} ({len(doc['items'])} строк)")
        audit.record("Проекты", "create", f"project #{project.id} ({project.title})", "title", "",
                     f"{title} (из MS Project)", project=project)
        result = {"projectId": project.id, "planId": plan.id}
    elif target == "plan":
        plan = create_plan(project, user, unique_plan_title(project, title), settings=importer.settings_for(doc))
        importer.fill(plan, user, doc, f"План создан импортом из MS Project: {file_name} ({len(doc['items'])} строк)")
        _audit_plan(project, plan, "create", new=f"{plan.title} (из MS Project)", field="title")
        result = {"projectId": project.id, "planId": plan.id}
    elif target == "replace":
        importer.check_revision(plan, _int("planRevision"))
        importer.replace_plan(plan, user, doc, file_name)
        _audit_plan(project, plan, "update", new=f"заменено из MS Project: {file_name}", field="content")
        result = {"projectId": project.id, "planId": plan.id}
    else:
        importer.check_revision(plan, _int("planRevision"))
        counts = importer.update_plan(plan, user, doc, options, file_name)
        _audit_plan(project, plan, "update", new=f"обновлено из MS Project: {file_name}", field="content")
        result = {"projectId": project.id, "planId": plan.id, **counts}
    db.session.commit()
    return jsonify(result)


def _next_project_position(direction_id):
    """Новый проект — в конец своего направления, как при создании вручную."""
    same = Project.direction_id.is_(None) if direction_id is None else Project.direction_id == direction_id
    return (db.session.execute(select(func.max(Project.position)).where(same)).scalar() or 0) + 1


@bp.get("/plans/<int:plan_id>/export/msproject")
@login_required
@limiter.limit("30 per minute", key_func=_user_key)
def export(plan_id):
    """План → MS Project XML. Комментарии строк — заметками задач, исполнители — ресурсами (с email сотрудников)."""
    plan, project, _level = _plan_with_access(plan_id)
    doc = snapshot_document(plan)
    doc["project"] = {"name": project.title, "title": plan.title}
    alive = {i["key"] for i in doc.get("items", [])}
    doc["comments"] = [{"itemKey": c.item_key, "text": c.text} for c in db.session.execute(
        select(ItemComment).where(ItemComment.plan_id == plan.id, ItemComment.deleted_at.is_(None))
        .order_by(ItemComment.id)).scalars() if c.item_key in alive]
    linked = {a.get("userId") for a in doc.get("assignees", []) if a.get("userId")}
    users = [{"id": u.id, "email": u.email} for u in db.session.execute(
        select(User).where(User.id.in_(linked))).scalars()] if linked else []
    days = db.session.execute(select(CalendarDay)).scalars().all()
    data = write_mspdi(doc, holidays={d.day: d.title for d in days if d.kind == "holiday"},
                       workdays={d.day: d.title for d in days if d.kind == "workday"}, users=users)
    name = re.sub(r'[\\/:*?"<>|]+', " ", f"{project.title} — {plan.title}").strip()[:150] or "plan"
    return Response(data, mimetype="application/xml", headers={
        "Content-Disposition": f"attachment; filename=\"plan.xml\"; filename*=UTF-8''{quote(name)}.xml",
        "Cache-Control": "no-store",
    })
