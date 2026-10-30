"""Структура и сотрудники из «По Делу» (ADR-0009) — тем же конвейером, что файлы podelu.* (ADR-0008).

1. ``fetch(client)`` — вход сервисной учётной записью, выгрузка структуры (``GET /api/org/export``),
   сотрудников (``GET /api/users/export``: ZIP с фото или JSON) и их идентификаторов
   (``GET /api/users/admin/all``), выход. Получается «снимок» — те же файлы, что скачал бы администратор.
2. ``preview(snapshot)`` — план без записи: план структуры, затем план сотрудников по структуре, какой она
   станет (структура применяется в транзакции, которая затем откатывается), — с теми же коллизиями и
   решениями, что у файла.
3. ``apply(snapshot)`` — то же одной транзакцией: структура, сотрудники, ссылки на «По Делу», фото. Правила
   — как у файлов: блокировка структуры, проверка циклов, права администратора из «По Делу» не выдаются,
   записи в реестре изменений.

Снимок между проверкой и применением хранит браузер администратора (как выбранный файл): решения по
коллизиям пересчитывают план без обращения к «По Делу». Применение заново получает данные из «По Делу» и
сверяет отпечаток: если там что-то изменилось после проверки, ничего не применяется, а план показывается заново.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from flask import current_app
from sqlalchemy import select

from app import audit
from app.extensions import db
from app.models import User
from app.podelu_client import NO_STRUCTURE_RIGHTS, NO_USERS_RIGHTS, PodeluError, is_uuid, rights
from app.services import employee_transfer, org, org_transfer

HEX64 = re.compile(r"^[0-9a-f]{64}$")
MAX_IDS = employee_transfer.MAX_RECORDS
# Поля, которые --prefer-podelu берёт из «По Делу» в коллизиях. Роль, логин и email — никогда: права и вход
# сотрудника в «Пульсе» меняет администратор (ADR-0008)
PREFER_FIELDS = ("name", "position", "department", "manager", "reportsToNone", "heads", "isActive", "avatar")


class SnapshotError(ValueError):
    """Снимок от браузера не разобран."""


@dataclass
class Options:
    apply_settings: bool = True
    new_users: str = "inactive"
    create_missing: bool = True
    photos: bool = True


@dataclass
class Snapshot:
    structure: dict
    employees: dict
    ids: dict = field(default_factory=dict)       # ключ записи сотрудника → id в «По Делу»
    avatars: dict = field(default_factory=dict)   # ключ записи → SHA-256 фото
    photos: dict = field(default_factory=dict)    # ключ записи → байты фото (только на сервере, при применении)
    source: dict = field(default_factory=dict)    # адрес, учётная запись, время — для показа

    def as_json(self):
        return {"structure": self.structure, "employees": self.employees, "ids": self.ids, "avatars": self.avatars,
                "source": self.source}

    def fingerprint(self):
        """Отпечаток данных «По Делу» (без времени выгрузки): изменилось ли что-то между проверкой и применением."""
        structure = {k: v for k, v in self.structure.items() if k != "exportedAt"}
        employees = {k: v for k, v in self.employees.items() if k != "exportedAt"}
        raw = json.dumps({"structure": structure, "employees": employees, "ids": self.ids, "avatars": self.avatars},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def item_key(item):
    """Ключ записи сотрудника — как employee_transfer.record_key: email, а без него — логин."""
    email = str(item.get("email") or "").strip().lower()[:255]
    if email:
        return email
    login = str(item.get("login") or "").strip()[:150]
    return f"login:{login.lower()}" if login else None


def _people_ids(document, people):
    """{ключ записи: id сотрудника в «По Делу»} — по email (без учёта регистра), а без email — по логину."""
    by_email, by_login = {}, {}
    for person in people:
        if not isinstance(person, dict) or person.get("isSystem") or not is_uuid(person.get("id")):
            continue
        email = str(person.get("email") or "").strip().lower()
        login = str(person.get("login") or "").strip().lower()
        if email:
            by_email.setdefault(email, person["id"])
        if login:
            by_login.setdefault(login, person["id"])
    ids = {}
    for item in document.get("employees") or []:
        if not isinstance(item, dict):
            continue
        key = item_key(item)
        email = str(item.get("email") or "").strip().lower()
        found = by_email.get(email) if email else by_login.get(str(item.get("login") or "").strip().lower())
        if key and found:
            ids[key] = found
    return ids


def fetch(client, *, photos=True):
    """Снимок «По Делу»: вход, проверка прав, выгрузки, выход (даже при ошибке)."""
    with client:
        user = client.login()
        allowed = rights(user)
        if not allowed["structure"]:
            raise PodeluError(NO_STRUCTURE_RIGHTS, 403)
        if not allowed["employees"]:
            raise PodeluError(NO_USERS_RIGHTS, 403)
        structure = client.export_structure()
        document, files = client.export_employees(photos=photos)
        people = client.people()
    avatars, photo_bytes = {}, {}
    for item in document.get("employees") or []:
        if not isinstance(item, dict):
            continue
        key, path = item_key(item), item.get("avatar")
        if key and isinstance(path, str) and path in files:
            photo_bytes[key] = files[path]
            avatars[key] = hashlib.sha256(files[path]).hexdigest()
    source = {"baseUrl": client.base_url, "account": str(user.get("name") or user.get("login") or "")[:255],
              "fetchedAt": _now_iso(), "app": str(structure.get("app") or document.get("app") or "")[:50]}
    return Snapshot(structure=structure, employees=document, ids=_people_ids(document, people), avatars=avatars,
                    photos=photo_bytes, source=source)


def snapshot_from_json(data):
    """Снимок, присланный браузером для пересчёта плана (решения по коллизиям) — проверка формы."""
    if not isinstance(data, dict) or not isinstance(data.get("structure"), dict) \
            or not isinstance(data.get("employees"), dict):
        raise SnapshotError("snapshot: нужны structure и employees из «По Делу»")
    ids, avatars = data.get("ids") or {}, data.get("avatars") or {}
    if not isinstance(ids, dict) or not isinstance(avatars, dict) or len(ids) > MAX_IDS or len(avatars) > MAX_IDS:
        raise SnapshotError("snapshot: ids и avatars — словари")
    ids = {str(key)[:300]: value for key, value in ids.items() if is_uuid(value)}
    avatars = {str(key)[:300]: value for key, value in avatars.items() if isinstance(value, str) and HEX64.match(value)}
    source = data.get("source") if isinstance(data.get("source"), dict) else {}
    source = {key: str(source.get(key) or "")[:500] for key in ("baseUrl", "account", "fetchedAt", "app")}
    return Snapshot(structure=data["structure"], employees=data["employees"], ids=ids, avatars=avatars, source=source)


def _records(snapshot):
    records = employee_transfer.parse_json(copy.deepcopy(snapshot.employees))
    for record in records:
        record.podelu_id = snapshot.ids.get(employee_transfer.record_key(record))
    return records


def _result(snapshot, structure, employees, options):
    counts = {"departments": len(snapshot.structure.get("departments") or []),
              "employees": len(snapshot.employees.get("employees") or []),
              "photos": len(snapshot.avatars), "linked": len(snapshot.ids)}
    return {
        "source": {**snapshot.source, **counts},
        "fingerprint": snapshot.fingerprint(),
        "structure": {**structure, "settingsApplied": options.apply_settings},
        "employees": employees,
        "canApply": bool(structure.get("canApply")) and bool(employees.get("canApply")),
    }


def preview(snapshot, *, actor_id, options, decisions=None):
    """План без записи в БД. Сотрудники планируются по структуре после загрузки: структура применяется в
    транзакции, которая откатывается (поэтому вызывающий не должен держать несохранённых изменений)."""
    records = _records(snapshot)
    try:
        structure, _applied = org_transfer.apply_structure(copy.deepcopy(snapshot.structure),
                                                           apply_settings=options.apply_settings, podelu=True)
        employees = employee_transfer.plan_employees(
            records, actor_id=actor_id, new_users=options.new_users, decisions=decisions, avatars=snapshot.avatars,
            create_missing=options.create_missing).as_dict()
    finally:
        db.session.rollback()
    return _result(snapshot, structure, employees, options)


def link_users(links):
    """{id сотрудника «Пульса»: id в «По Делу»} → podelu_id; ссылка другого сотрудника на того же человека
    «По Делу» снимается. Возвращает число новых ссылок."""
    wanted = set(links.values())
    for user in db.session.execute(select(User).where(User.podelu_id.is_not(None))).scalars():
        if user.podelu_id in wanted and links.get(user.id) != user.podelu_id:
            user.podelu_id = None
    db.session.flush()
    changed = 0
    for user_id, podelu_id in links.items():
        user = db.session.get(User, user_id)
        if user is not None and user.podelu_id != podelu_id:
            user.podelu_id = podelu_id
            changed += 1
    db.session.flush()
    return changed


def apply(snapshot, *, actor_id, options, decisions=None):
    """(результат, применено ли, [id отключённых]). Всё — в текущей транзакции; при ошибках в плане ничего не
    меняется (вызывающий откатывает). Фиксирует транзакцию вызывающий — после org.assert_consistent()."""
    records = _records(snapshot)
    org.lock_org()
    structure, applied = org_transfer.apply_structure(copy.deepcopy(snapshot.structure),
                                                      apply_settings=options.apply_settings, podelu=True)
    if not applied:
        employees = employee_transfer.plan_employees(
            records, actor_id=actor_id, new_users=options.new_users, decisions=decisions, avatars=snapshot.avatars,
            create_missing=options.create_missing).as_dict()
        return _result(snapshot, structure, employees, options), False, []
    employees, applied, mapping, upload, deactivated = employee_transfer.apply_employees(
        records, actor_id=actor_id, new_users=options.new_users, decisions=decisions, avatars=snapshot.avatars,
        create_missing=options.create_missing, source="podelu")
    result = _result(snapshot, structure, employees, options)
    if not applied:
        return result, False, []
    linked = link_users({mapping[key]: podelu_id for key, podelu_id in snapshot.ids.items() if key in mapping})
    photos = {str(mapping[key]): snapshot.photos[key] for key in upload if key in snapshot.photos and key in mapping}
    errors, saved = employee_transfer.set_avatars(photos, max_bytes=current_app.config["AVATAR_MAX_BYTES"]) \
        if photos else ({}, 0)
    s, e = structure["summary"], employees["summary"]
    audit.record("Структура", "import", "Загрузка из «По Делу»", "", "",
                 f"{snapshot.source.get('baseUrl', '')}: подразделений новых {s['create']}, изменено {s['update']}; "
                 f"сотрудников новых {e['create']}, обновлено {e['update']}, пропущено {e['skip']}; фото {saved}")
    result.update({"applied": True, "ids": mapping, "linkedUsers": linked,
                   "photos": {"saved": saved, "errors": sorted(set(errors.values()))}})
    return result, True, deactivated


def prefer_podelu_decisions(employees_plan):
    """Решения «взять из «По Делу»» для коллизий в полях PREFER_FIELDS — для синхронизации по расписанию."""
    decisions = {}
    for record in employees_plan.get("records") or []:
        for conflict in record.get("conflicts") or []:
            if conflict["field"] in PREFER_FIELDS and "incoming" in conflict.get("choices", []):
                decisions.setdefault(record["key"], {"fields": {}})["fields"][conflict["field"]] = {"choice": "incoming"}
    return decisions
