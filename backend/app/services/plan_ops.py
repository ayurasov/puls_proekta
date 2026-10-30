"""Применение пакета операций к плану (ADR-0002).

Клиент не присылает план целиком (как в 1.0.x, где последнее сохранение молча затирало
правки коллег), а присылает операции: создать/изменить/удалить/вернуть строку, пометку,
исполнителя; изменить параметры плана. Пакет применяется атомарно:

1. план блокируется (`SELECT … FOR UPDATE` в PostgreSQL) — номера ревизий не повторяются;
2. при необходимости делается автоматический снимок состояния «до»;
3. операции применяются по полям: правка одного поля не трогает остальные, поэтому
   одновременная работа нескольких сотрудников над разными строками и полями не теряет
   ничьих изменений; если строку после `baseRevision` менял другой сотрудник,
   ответ содержит `conflicts` — интерфейс сообщает об этом;
4. проверяется целостность ссылок (родитель, предшественник, исполнитель, строка пометки);
   ссылки на удалённое или циклы исправляются и попадают в `fixes`;
5. каждая изменённая сущность получает номер новой ревизии, каждое изменение —
   запись журнала (кто, когда, что было и что стало), пакет — строку реестра изменений.
"""

import secrets
from datetime import date

from flask import current_app
from sqlalchemy import select

from app import audit
from app.extensions import db
from app.models import Plan, PlanAssignee, PlanEvent, PlanItem, PlanNote, User, utcnow
from app.security import ApiError
from app.services.plan_doc import maybe_auto_snapshot
from app.services.plan_schema import (
    FIELD_LABELS, ITEM_FIELDS, STATUS_LABELS, TYPE_LABELS, assignee_to_api, check_key, convert_field,
    is_color, item_to_api, normalize_settings, note_to_api, parse_date,
)

MAX_OPS = 2000

TASK_DEFAULTS = {
    "status": "new", "end_mode": "date", "duration_type": "calendar", "progress": 0, "offset_days": 0,
    "dependency_type": "FS", "dependency_lag": 0, "color": "blue",
}
MILESTONE_DEFAULTS = {"line_style": "dashed", "color": "teal"}
GROUP_DEFAULTS = {"color": "slate"}


def new_key(prefix):
    return f"{prefix}{secrets.token_hex(5)}"


def fmt_date(value):
    if value is None:
        return "—"
    if isinstance(value, str):
        value = date.fromisoformat(value)
    return value.strftime("%d.%m.%Y")


class OpsResult:
    def __init__(self, revision):
        self.revision = revision
        self.items = {}
        self.notes = {}
        self.assignees = {}
        self.plan_changed = False
        self.conflicts = []
        self.fixes = []
        self.events = []

    def as_dict(self):
        return {
            "revision": self.revision,
            "items": [item_to_api(i) for i in self.items.values()],
            "notes": [note_to_api(n) for n in self.notes.values()],
            "assignees": [assignee_to_api(a) for a in self.assignees.values()],
            "planChanged": self.plan_changed,
            "conflicts": self.conflicts,
            "fixes": self.fixes,
        }


class PlanEditor:
    def __init__(self, plan, user, base_revision=None):
        self.plan = plan
        self.user = user
        self.base_revision = base_revision
        self.now = utcnow()
        self.items = {i.key: i for i in db.session.execute(select(PlanItem).where(PlanItem.plan_id == plan.id)).scalars()}
        self.notes = {n.key: n for n in db.session.execute(select(PlanNote).where(PlanNote.plan_id == plan.id)).scalars()}
        self.assignees = {a.key: a for a in db.session.execute(
            select(PlanAssignee).where(PlanAssignee.plan_id == plan.id)).scalars()}
        self.revision = plan.revision + 1
        self.result = OpsResult(self.revision)
        self.pending_events = []
        self.touched = set()

    # ---------- общие помощники ----------
    def _alive(self, collection, key):
        entity = collection.get(key)
        return entity if entity is not None and entity.deleted_at is None else None

    def _label(self, item):
        return f"{TYPE_LABELS.get(item.type, 'Строка')} «{(item.name or '').strip() or item.key}»"

    def _event(self, entity, key, action, summary, changes=None):
        self.pending_events.append(PlanEvent(
            plan_id=self.plan.id, revision=self.revision, entity=entity, entity_key=key, action=action,
            user_id=self.user.id if self.user else None, user_name=self.user.name if self.user else "система",
            created_at=self.now, summary=summary, changes=changes,
        ))

    def _touch(self, entity, bucket):
        entity.updated_at = self.now
        entity.updated_by_id = self.user.id if self.user else None
        entity.updated_rev = self.revision
        bucket[entity.key] = entity
        self.touched.add(id(entity))

    def _conflict(self, entity, kind):
        if self.base_revision is None:
            return
        if entity.updated_rev > self.base_revision and entity.updated_by_id and (
                self.user is None or entity.updated_by_id != self.user.id):
            self.result.conflicts.append({"entity": kind, "key": entity.key, "byId": entity.updated_by_id})

    def _describe(self, api_name, value):
        if value is None or value == "":
            return "—"
        if api_name == "status":
            return STATUS_LABELS.get(value, value)
        if api_name in ("start", "end", "date", "completedDate"):
            return fmt_date(value)
        if api_name == "progress":
            return f"{value}%"
        if api_name in ("parentKey", "dependencyKey"):
            ref = self.items.get(value)
            return f"«{ref.name}»" if ref is not None else value
        if api_name == "assigneeKey":
            ref = self.assignees.get(value)
            return ref.name if ref is not None else value
        if isinstance(value, date):
            return fmt_date(value)
        return str(value)

    def _replayed_create(self, collection, key, data, update):
        """Повтор создания тем же сотрудником — пакет уже применён, но ответ до клиента не дошёл
        (обрыв связи, 502), и клиент отправил его снова. Ключи случайные, у разных сотрудников не
        совпадают, поэтому повтор безопасно применить как правку тех же полей (или пропустить, если
        строку уже удалили следующей правкой). Иначе — True не возвращаем, и создание отклоняется."""
        existing = collection.get(key)
        if existing is None or self.user is None or existing.created_by_id != self.user.id:
            return False
        if existing.deleted_at is None:
            update({"key": key, "fields": {k: v for k, v in data.items() if k not in ("key", "type", "extra")}})
        return True

    # ---------- строки ----------
    def _set_item_fields(self, item, fields, creating=False):
        changes = {}
        for api_name, value in fields.items():
            if api_name not in ITEM_FIELDS:
                continue
            if api_name == "date" and item.type != "milestone":
                continue
            if api_name in ("start", "end") and item.type == "milestone":
                continue
            attr, converted = convert_field(api_name, value)
            old = getattr(item, attr)
            if old != converted:
                setattr(item, attr, converted)
                if api_name != "position":
                    changes[api_name] = [self._json(old), self._json(converted)]
                else:
                    changes["position"] = [old, converted]
        if not creating:
            self._validate_item(item)
        return changes

    @staticmethod
    def _json(value):
        if isinstance(value, date):
            return value.isoformat()
        return value

    def _validate_item(self, item):
        if item.type == "task":
            if item.start_date is None or item.end_date is None:
                raise ApiError(400, "validation_error", f"У задачи «{item.name}» должны быть даты начала и окончания")
            if item.end_date < item.start_date:
                raise ApiError(400, "validation_error", f"Окончание задачи «{item.name}» раньше начала")
        elif item.type == "milestone" and item.start_date is None:
            raise ApiError(400, "validation_error", f"У вехи «{item.name}» должна быть дата")

    def op_item_create(self, op):
        data = op.get("item") or {}
        item_type = data.get("type")
        if item_type not in ("task", "milestone", "group"):
            raise ApiError(400, "validation_error", "Неизвестный тип строки")
        key = data.get("key") or new_key("i")
        check_key(key)
        if key in self.items:
            if self._replayed_create(self.items, key, data, self.op_item_update):
                return
            existing = self.items[key]
            if existing.deleted_at is not None:
                raise ApiError(409, "key_exists", "Строка с таким ключом удалена — используйте восстановление")
            raise ApiError(409, "key_exists", "Строка с таким ключом уже есть")
        item = PlanItem(plan_id=self.plan.id, key=key, type=item_type, name="",
                        created_at=self.now, created_by_id=self.user.id if self.user else None,
                        comment_count=0, extra=data.get("extra") if isinstance(data.get("extra"), dict) else None)
        defaults = {"task": TASK_DEFAULTS, "milestone": MILESTONE_DEFAULTS, "group": GROUP_DEFAULTS}[item_type]
        for attr, value in defaults.items():
            setattr(item, attr, value)
        if "position" not in data:
            alive = [i.position for i in self.items.values() if i.deleted_at is None]
            item.position = (max(alive) + 1024) if alive else 1024
        self._set_item_fields(item, {k: v for k, v in data.items() if k not in ("key", "type", "extra")}, creating=True)
        if item_type == "task" and item.end_mode == "duration" and item.duration is None and item.start_date and item.end_date:
            item.duration = (item.end_date - item.start_date).days + 1
        if item_type == "milestone" and item.line_color is None:
            item.line_color = item.color
        self._validate_item(item)
        if not (item.name or "").strip():
            raise ApiError(400, "validation_error", "Укажите название")
        db.session.add(item)
        self.items[key] = item
        self._touch(item, self.result.items)
        self._event("item", key, "create", f"{self._label(item)} создана" if item_type != "milestone"
                    else f"{self._label(item)} создана", {"type": item_type})

    def op_item_update(self, op):
        key = op.get("key")
        item = self._alive(self.items, key)
        if item is None:
            raise ApiError(409, "gone", "Строка удалена другим сотрудником", details={"key": key})
        fields = op.get("fields") or {}
        if not isinstance(fields, dict):
            raise ApiError(400, "validation_error", "Неверный формат изменений")
        self._conflict(item, "item")
        before_name = self._label(item)
        changes = self._set_item_fields(item, fields)
        if not changes:
            return
        self._touch(item, self.result.items)
        parts = []
        if "start" in changes or "end" in changes:
            def was(name, value):
                # Прежнее значение: из изменений, иначе текущее (у группы дат может не быть)
                return changes[name][0] if name in changes else (value.isoformat() if value else None)
            old_s = was("start", item.start_date)
            old_e = was("end", item.end_date)
            parts.append(f"Сроки: {fmt_date(old_s)}–{fmt_date(old_e)} → {fmt_date(item.start_date)}–{fmt_date(item.end_date)}")
        for api_name, (old, new) in changes.items():
            if api_name in ("start", "end", "position"):
                continue
            if api_name == "parentKey":
                parts.append(f"Перемещена в {self._describe('parentKey', new)}" if new else "Вынесена на верхний уровень")
                continue
            parts.append(f"{FIELD_LABELS.get(api_name, api_name)}: {self._describe(api_name, old)} → {self._describe(api_name, new)}")
        if not parts:
            parts.append("Изменён порядок строк")
        self._event("item", key, "update", f"{before_name}: " + "; ".join(parts), changes)

    def op_item_delete(self, op):
        key = op.get("key")
        item = self._alive(self.items, key)
        if item is None:
            return
        item.deleted_at = self.now
        item.deleted_by_id = self.user.id if self.user else None
        self._touch(item, self.result.items)
        self._event("item", key, "delete", f"{self._label(item)} удалена")

    def op_item_restore(self, op):
        key = op.get("key")
        item = self.items.get(key)
        if item is None or item.deleted_at is None:
            return
        item.deleted_at = None
        item.deleted_by_id = None
        self._touch(item, self.result.items)
        self._event("item", key, "restore", f"{self._label(item)} восстановлена")

    # ---------- пометки ----------
    def _note_fields(self, note, fields):
        changes = {}
        for name, value in fields.items():
            if name == "date":
                new = parse_date(value, "Дата пометки")
                if new is None:
                    raise ApiError(400, "validation_error", "Укажите дату пометки")
                attr = "date"
            elif name == "text":
                if not isinstance(value, str) or not value.strip():
                    raise ApiError(400, "validation_error", "Укажите текст пометки")
                new, attr = value.strip()[:5000], "text"
            elif name == "rowKey":
                new, attr = (check_key(value, "строка") if value else None), "row_key"
            elif name == "color":
                if value and not is_color(value):
                    raise ApiError(400, "validation_error", "Неверный цвет")
                new, attr = value or None, "color"
            elif name == "showDate":
                new, attr = bool(value), "show_date"
            else:
                continue
            old = getattr(note, attr)
            if old != new:
                setattr(note, attr, new)
                changes[name] = [self._json(old), self._json(new)]
        return changes

    def op_note_create(self, op):
        data = op.get("note") or {}
        key = data.get("key") or new_key("n")
        check_key(key)
        if key in self.notes:
            if self._replayed_create(self.notes, key, data, self.op_note_update):
                return
            raise ApiError(409, "key_exists", "Пометка с таким ключом уже есть")
        note = PlanNote(plan_id=self.plan.id, key=key, date=date.today(), text="", show_date=False,
                        created_at=self.now, created_by_id=self.user.id if self.user else None, color="orange")
        if "date" not in data or "text" not in data:
            raise ApiError(400, "validation_error", "Укажите дату и текст пометки")
        self._note_fields(note, {k: v for k, v in data.items() if k != "key"})
        db.session.add(note)
        self.notes[key] = note
        self._touch(note, self.result.notes)
        self._event("note", key, "create", f"Пометка «{note.text[:80]}» на {fmt_date(note.date)} добавлена")

    def op_note_update(self, op):
        note = self._alive(self.notes, op.get("key"))
        if note is None:
            raise ApiError(409, "gone", "Пометка удалена другим сотрудником", details={"key": op.get("key")})
        self._conflict(note, "note")
        changes = self._note_fields(note, op.get("fields") or {})
        if changes:
            self._touch(note, self.result.notes)
            self._event("note", note.key, "update", f"Пометка «{note.text[:80]}» изменена", changes)

    def op_note_delete(self, op):
        note = self._alive(self.notes, op.get("key"))
        if note is None:
            return
        note.deleted_at = self.now
        note.deleted_by_id = self.user.id if self.user else None
        self._touch(note, self.result.notes)
        self._event("note", note.key, "delete", f"Пометка «{note.text[:80]}» удалена")

    def op_note_restore(self, op):
        note = self.notes.get(op.get("key"))
        if note is None or note.deleted_at is None:
            return
        note.deleted_at = None
        self._touch(note, self.result.notes)
        self._event("note", note.key, "restore", f"Пометка «{note.text[:80]}» восстановлена")

    # ---------- исполнители ----------
    def _assignee_fields(self, assignee, fields):
        changes = {}
        for name, value in fields.items():
            if name == "name":
                if not isinstance(value, str) or not value.strip():
                    raise ApiError(400, "validation_error", "Укажите ФИО исполнителя")
                new, attr = " ".join(value.split())[:500], "name"
            elif name == "role":
                new, attr = (" ".join((value or "").split()))[:500], "role"
            elif name == "color":
                if not isinstance(value, str) or not is_color(value):
                    raise ApiError(400, "validation_error", "Неверный цвет")
                new, attr = value, "color"
            elif name == "userId":
                if value in (None, ""):
                    new = None
                else:
                    try:
                        new = int(value)
                    except (TypeError, ValueError):
                        raise ApiError(400, "validation_error", "Неверный сотрудник исполнителя") from None
                    if db.session.get(User, new) is None:
                        raise ApiError(400, "validation_error", "Сотрудник исполнителя не найден")
                attr = "user_id"
            elif name == "position":
                if not isinstance(value, (int, float)):
                    raise ApiError(400, "validation_error", "Неверный порядок")
                new, attr = float(value), "position"
            else:
                continue
            old = getattr(assignee, attr)
            if old != new:
                setattr(assignee, attr, new)
                changes[name] = [old, new]
        return changes

    def op_assignee_create(self, op):
        data = op.get("assignee") or {}
        key = data.get("key") or new_key("a")
        check_key(key)
        if key in self.assignees:
            if self._replayed_create(self.assignees, key, data, self.op_assignee_update):
                return
            raise ApiError(409, "key_exists", "Исполнитель с таким ключом уже есть")
        assignee = PlanAssignee(plan_id=self.plan.id, key=key, name="", role="", color="#2563eb",
                                created_at=self.now, created_by_id=self.user.id if self.user else None)
        if "position" not in data:
            alive = [a.position for a in self.assignees.values() if a.deleted_at is None]
            assignee.position = (max(alive) + 1) if alive else 1
        self._assignee_fields(assignee, {k: v for k, v in data.items() if k != "key"})
        if not assignee.name:
            raise ApiError(400, "validation_error", "Укажите ФИО исполнителя")
        db.session.add(assignee)
        self.assignees[key] = assignee
        self._touch(assignee, self.result.assignees)
        self._event("assignee", key, "create", f"Исполнитель «{assignee.name}» добавлен")

    def op_assignee_update(self, op):
        assignee = self._alive(self.assignees, op.get("key"))
        if assignee is None:
            raise ApiError(409, "gone", "Исполнитель удалён другим сотрудником", details={"key": op.get("key")})
        changes = self._assignee_fields(assignee, op.get("fields") or {})
        if changes:
            self._touch(assignee, self.result.assignees)
            self._event("assignee", assignee.key, "update", f"Исполнитель «{assignee.name}» изменён", changes)

    def op_assignee_delete(self, op):
        assignee = self._alive(self.assignees, op.get("key"))
        if assignee is None:
            return
        assignee.deleted_at = self.now
        self._touch(assignee, self.result.assignees)
        self._event("assignee", assignee.key, "delete", f"Исполнитель «{assignee.name}» удалён")

    def op_assignee_restore(self, op):
        assignee = self.assignees.get(op.get("key"))
        if assignee is None or assignee.deleted_at is None:
            return
        assignee.deleted_at = None
        self._touch(assignee, self.result.assignees)
        self._event("assignee", assignee.key, "restore", f"Исполнитель «{assignee.name}» восстановлен")

    # ---------- параметры плана ----------
    def op_plan_update(self, op):
        fields = op.get("fields") or {}
        changes = {}
        if "title" in fields:
            title = " ".join(str(fields["title"] or "").split())[:500]
            if not title:
                raise ApiError(400, "validation_error", "Укажите название плана")
            if title != self.plan.title:
                changes["title"] = [self.plan.title, title]
                self.plan.title = title
        if "subtitle" in fields:
            subtitle = str(fields["subtitle"] or "").strip()[:1000]
            if subtitle != (self.plan.subtitle or ""):
                changes["subtitle"] = [self.plan.subtitle, subtitle]
                self.plan.subtitle = subtitle
        if "settings" in fields:
            current = normalize_settings(self.plan.settings)
            updated = normalize_settings(fields["settings"], base=current)
            if updated != current:
                diff = {k: [current.get(k), updated.get(k)] for k in updated if current.get(k) != updated.get(k)}
                changes["settings"] = diff
                self.plan.settings = updated
        if changes:
            self.plan.meta_rev = self.revision
            self.result.plan_changed = True
            labels = {"title": "название", "subtitle": "подзаголовок", "settings": "настройки графика"}
            self._event("plan", None, "update", "Изменены " + ", ".join(labels[k] for k in changes) + " плана", changes)

    # ---------- целостность ----------
    def _fix(self, entity, kind, field, reason):
        self.result.fixes.append({"entity": kind, "key": entity.key, "field": field, "reason": reason})

    def check_integrity(self):
        alive_items = {k: i for k, i in self.items.items() if i.deleted_at is None}
        for item in alive_items.values():
            changed = False
            if item.parent_key is not None:
                parent = alive_items.get(item.parent_key)
                if parent is None or parent.key == item.key or parent.type == "milestone":
                    self._fix(item, "item", "parentKey", "родитель удалён или недопустим")
                    item.parent_key = None
                    changed = True
            if item.dependency_key is not None:
                dep = alive_items.get(item.dependency_key)
                if dep is None or dep.key == item.key or dep.type == "group" or item.type != "task":
                    self._fix(item, "item", "dependencyKey", "предшественник удалён или недопустим")
                    item.dependency_key = None
                    changed = True
            if item.assignee_key is not None and self._alive(self.assignees, item.assignee_key) is None:
                self._fix(item, "item", "assigneeKey", "исполнитель удалён")
                item.assignee_key = None
                changed = True
            if changed:
                self._touch(item, self.result.items)
        # Циклы в дереве: разрываем на строке, где цикл обнаружен
        for item in alive_items.values():
            seen = {item.key}
            node = item
            while node.parent_key is not None:
                if node.parent_key in seen:
                    self._fix(node, "item", "parentKey", "цикл во вложенности")
                    node.parent_key = None
                    self._touch(node, self.result.items)
                    break
                seen.add(node.parent_key)
                node = alive_items.get(node.parent_key)
                if node is None:
                    break
        # Циклы связей «предшественник»
        for item in alive_items.values():
            seen = {item.key}
            node = item
            while node is not None and node.dependency_key is not None:
                if node.dependency_key in seen:
                    self._fix(node, "item", "dependencyKey", "циклическая связь")
                    node.dependency_key = None
                    self._touch(node, self.result.items)
                    break
                seen.add(node.dependency_key)
                node = alive_items.get(node.dependency_key)
        for note in self.notes.values():
            if note.deleted_at is None and note.row_key is not None and note.row_key not in alive_items:
                # Пометка строки, которой больше нет, удаляется вместе со строкой (как в 1.0.x)
                note.deleted_at = self.now
                self._fix(note, "note", "rowKey", "строка удалена")
                self._touch(note, self.result.notes)

    # ---------- применение ----------
    def apply(self, ops):
        if not isinstance(ops, list) or not ops:
            raise ApiError(400, "validation_error", "Нет операций")
        if len(ops) > MAX_OPS:
            raise ApiError(400, "validation_error", "Слишком много операций в одном пакете")
        handlers = {
            "item.create": self.op_item_create, "item.update": self.op_item_update,
            "item.delete": self.op_item_delete, "item.restore": self.op_item_restore,
            "note.create": self.op_note_create, "note.update": self.op_note_update,
            "note.delete": self.op_note_delete, "note.restore": self.op_note_restore,
            "assignee.create": self.op_assignee_create, "assignee.update": self.op_assignee_update,
            "assignee.delete": self.op_assignee_delete, "assignee.restore": self.op_assignee_restore,
            "plan.update": self.op_plan_update,
        }
        for op in ops:
            if not isinstance(op, dict) or op.get("op") not in handlers:
                raise ApiError(400, "validation_error", f"Неизвестная операция: {op.get('op') if isinstance(op, dict) else op}")
            handlers[op["op"]](op)
        self.check_integrity()
        return self.pending_events


def _begin_immediate_on_sqlite():
    """SQLite не знает SELECT … FOR UPDATE: блокировку записи берём сразу, BEGIN IMMEDIATE.

    Иначе два одновременных пакета на SQLite (разработка, e2e, небольшие стенды) оба читали бы план
    до записи и получали одну ревизию, а правка одного и того же поля не распознавалась бы как
    одновременная. Второй запрос ждёт первого (busy_timeout, extensions.py). Если в транзакции уже
    была запись, блокировка и так у неё. В PostgreSQL то же делает with_for_update().
    """
    connection = db.session.connection()
    if connection.dialect.name != "sqlite":
        return
    raw = connection.connection.driver_connection
    if not raw.in_transaction:
        raw.execute("BEGIN IMMEDIATE")


def lock_plan(plan_id, allow_archived=False):
    """Заблокировать строку плана до конца транзакции и перечитать её.

    Объект плана обычно уже загружен в сессию проверкой доступа — до того, как блокировка получена.
    Без populate_existing сессия вернула бы прежний объект: ревизия, настройки и время снимка остались бы
    прочитанными до блокировки, два одновременных пакета получили бы одну ревизию, а коллега пропустил
    бы чужую правку при опросе. Состояние (удалён, в архиве) проверяется уже после блокировки.
    """
    _begin_immediate_on_sqlite()
    plan = db.session.execute(
        select(Plan).where(Plan.id == plan_id).with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if plan is None or plan.deleted_at is not None:
        raise ApiError(404, "not_found", "План не найден")
    if not allow_archived and plan.is_archived:
        raise ApiError(409, "archived", "План в архиве — сначала верните его из архива")
    return plan


def apply_ops(plan, user, ops, base_revision=None, snapshot=True, audit_summary=True, project=None):
    """Применить пакет; вернуть OpsResult. План должен быть заблокирован `lock_plan`."""
    if snapshot:
        maybe_auto_snapshot(plan, user, current_app.config.get("SNAPSHOT_INTERVAL_MINUTES", 30))
    editor = PlanEditor(plan, user, base_revision)
    events = editor.apply(ops)
    result = editor.result
    if not events and not result.fixes and not result.plan_changed:
        result.revision = plan.revision
        return result
    plan.revision = editor.revision
    plan.updated_at = editor.now
    plan.updated_by_id = user.id if user else None
    db.session.add_all(events)
    result.events = events
    if audit_summary and events:
        project = project or plan.project
        first = events[0].summary
        more = f" (и ещё {len(events) - 1})" if len(events) > 1 else ""
        audit.record("Гант", "update", f"plan #{plan.id} ({plan.title})", "", "", first + more, project=project,
                     details={"planId": plan.id, "revision": plan.revision, "changes": len(events)})
    if project is not None:
        project.updated_at = editor.now
    elif plan.project is not None:
        plan.project.updated_at = editor.now
    return result
