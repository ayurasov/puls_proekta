"""Применение пакета операций к плану (ADR-0002).

Клиент не присылает план целиком (как в 1.0.x, где последнее сохранение молча затирало
правки коллег), а присылает операции: создать/изменить/удалить/вернуть строку, связь, пометку,
исполнителя; изменить параметры плана. Пакет применяется атомарно:

1. план блокируется (`SELECT … FOR UPDATE` в PostgreSQL) — номера ревизий не повторяются;
2. при необходимости делается автоматический снимок состояния «до»;
3. операции применяются по полям: правка одного поля не трогает остальные, поэтому
   одновременная работа нескольких сотрудников над разными строками и полями не теряет
   ничьих изменений; если строку после `baseRevision` менял другой сотрудник,
   ответ содержит `conflicts` — интерфейс сообщает об этом;
4. проверяется целостность ссылок (родитель, исполнитель, строки связи и пометки); ссылки на
   удалённое или циклы вложенности исправляются и попадают в `fixes`. Связь удалённой строки
   удаляется вместе с ней и возвращается при её восстановлении; цикл связей не исправляется,
   а не допускается: операция, которая замкнула бы цикл, отклоняется;
5. каждая изменённая сущность получает номер новой ревизии, каждое изменение —
   запись журнала (кто, когда, что было и что стало), пакет — строку реестра изменений.
"""

import secrets
from datetime import date

from flask import current_app
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select

from app import audit
from app.extensions import db
from app.models import Plan, PlanAssignee, PlanEvent, PlanItem, PlanLink, PlanNote, User, utcnow
from app.security import ApiError
from app.services.plan_doc import maybe_auto_snapshot
from app.services.plan_schema import (
    CONSTRAINT_LABELS, CONSTRAINTS_WITHOUT_DATE, FIELD_LABELS, ITEM_FIELDS, LAG_KIND_LABELS, LEGACY_DEPENDENCY_FIELDS,
    LINK_TYPE_LABELS, LINK_TYPES, MAX_LAG, SCHEDULE_ONLY_FIELDS, STATUS_LABELS, TYPE_LABELS, assignee_to_api,
    check_key, convert_field, is_color, item_to_api, link_to_api, normalize_settings, note_to_api, parse_date,
)

MAX_OPS = 2000

TASK_DEFAULTS = {
    "status": "new", "end_mode": "date", "duration_type": "calendar", "progress": 0, "offset_days": 0, "color": "blue",
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
        self.links = {}
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
            "links": [link_to_api(link) for link in self.links.values()],
            "notes": [note_to_api(n) for n in self.notes.values()],
            "assignees": [assignee_to_api(a) for a in self.assignees.values()],
            "planChanged": self.plan_changed,
            "conflicts": self.conflicts,
            "fixes": self.fixes,
        }


class PlanEditor:
    """Применение операций к плану.

    trusted — пакет собирает сервер (копия плана, импорт файла, восстановление версии), а не браузер: только такой
    пакет пишет идентификаторы строк в MS Project (`extra.msp`), по которым обновление из файла находит строки.
    revive_links — восстановленная строка возвращает свои связи, удалённые вместе с ней (отмена удаления); при
    восстановлении версии плана связи берутся только из версии.
    """

    def __init__(self, plan, user, base_revision=None, trusted=False, revive_links=True):
        self.plan = plan
        self.user = user
        self.base_revision = base_revision
        self.now = utcnow()
        self.items = {i.key: i for i in db.session.execute(select(PlanItem).where(PlanItem.plan_id == plan.id)).scalars()}
        self.notes = {n.key: n for n in db.session.execute(select(PlanNote).where(PlanNote.plan_id == plan.id)).scalars()}
        self.assignees = {a.key: a for a in db.session.execute(
            select(PlanAssignee).where(PlanAssignee.plan_id == plan.id)).scalars()}
        self.links = {link.key: link for link in db.session.execute(
            select(PlanLink).where(PlanLink.plan_id == plan.id).order_by(PlanLink.id)).scalars()}
        # Граф живых связей: последующие строки и связь по паре строк (проверка повторов и циклов)
        self._succ = {}
        self._pairs = {}
        for link in self.links.values():
            if link.deleted_at is None:
                self._graph_add(link)
        self.revision = plan.revision + 1
        self.result = OpsResult(self.revision)
        self.pending_events = []
        self.touched = set()
        # Восстановленные в пакете строки → когда они были удалены: их связи возвращаются вместе с ними
        self.restored = {}
        # Предшественники из полей строк до 0.7.0 — связи создаются после всех операций пакета
        self.legacy_links = []
        # Заполнение плана из файла: связь, которую нельзя создать (цикл, повтор, нет строки), пропускается с
        # отметкой в fixes, а не отклоняет весь файл
        self.skip_invalid_links = False
        self.trusted = trusted
        self.revive_links = revive_links
        # Связи, ставшие живыми или сменившие концы в пакете: цикл проверяется один раз, по итоговому графу пакета
        # (проверка каждой связи обходом графа на больших планах квадратична). id(связи) → [связь, строго, откат]
        self._cycle_checks = {}

    # ---------- общие помощники ----------
    def _alive(self, collection, key):
        entity = collection.get(key)
        return entity if entity is not None and entity.deleted_at is None else None

    def _label(self, item):
        return f"{TYPE_LABELS.get(item.type, 'Строка')} «{(item.name or '').strip() or item.key}»"

    def _event(self, entity, key, action, summary, changes=None):
        event = PlanEvent(
            plan_id=self.plan.id, revision=self.revision, entity=entity, entity_key=key, action=action,
            user_id=self.user.id if self.user else None, user_name=self.user.name if self.user else "система",
            created_at=self.now, summary=summary, changes=changes,
        )
        self.pending_events.append(event)
        return event

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
        if api_name in ("start", "end", "date", "completedDate", "deadline", "constraintDate"):
            return fmt_date(value)
        if api_name == "progress":
            return f"{value}%"
        if api_name == "constraintType":
            return CONSTRAINT_LABELS.get(value, value)
        if api_name == "parentKey":
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
            if api_name in SCHEDULE_ONLY_FIELDS and item.type == "group":
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
        if item.type in ("task", "milestone"):
            if item.constraint_type is None and item.constraint_date is not None:
                raise ApiError(400, "validation_error", f"Укажите тип ограничения даты строки «{item.name}»")
            if item.constraint_type and item.constraint_type not in CONSTRAINTS_WITHOUT_DATE \
                    and item.constraint_date is None:
                raise ApiError(400, "validation_error", f"Укажите дату ограничения "
                               f"«{CONSTRAINT_LABELS[item.constraint_type]}» строки «{item.name}»")

    @staticmethod
    def _split_legacy(data):
        """Поля операции строки без полей предшественника до 0.7.0 и сами эти поля (если переданы)."""
        legacy = {k: data[k] for k in LEGACY_DEPENDENCY_FIELDS if k in data}
        if legacy.get("dependencyType") not in (None, "", *LINK_TYPES):
            raise ApiError(400, "validation_error", "Неизвестный тип связи")
        lag = legacy.get("dependencyLag")
        if lag not in (None, "") and (not isinstance(lag, int) or isinstance(lag, bool) or abs(lag) > MAX_LAG):
            raise ApiError(400, "validation_error", "Лаг связи должен быть целым числом дней")
        if legacy.get("dependencyKey") not in (None, ""):
            check_key(legacy["dependencyKey"], "предшественник")
        return {k: v for k, v in data.items() if k not in LEGACY_DEPENDENCY_FIELDS}, legacy

    def _legacy_dependency(self, item, legacy):
        """Правка единственного предшественника строки полями до 0.7.0 (dependencyKey/Type/Lag): старые документы,
        скрипты, страница, открытая до обновления. Переводится в операции связей; если предшественников у строки
        уже несколько, правка неоднозначна и отклоняется."""
        incoming = [link for link in self.links.values() if link.deleted_at is None and link.to_key == item.key]
        if len(incoming) > 1:
            raise ApiError(400, "validation_error", f"У строки «{item.name}» несколько предшественников — связи меняются "
                           "в карточке строки, в разделе «Связи». Обновите страницу")
        current = incoming[0] if incoming else None
        wanted = legacy.get("dependencyKey", current.from_key if current else None) or None
        link_type = legacy.get("dependencyType") or (current.type if current else "FS")
        lag = legacy.get("dependencyLag", current.lag if current else 0) or 0
        if wanted is None:
            if current is not None:
                self.op_link_delete({"key": current.key})
            return
        if current is not None and current.from_key == wanted:
            self.op_link_update({"key": current.key, "fields": {"type": link_type, "lag": lag}})
            return
        if current is not None:
            self.op_link_delete({"key": current.key})
        self.op_link_create({"link": {"from": wanted, "to": item.key, "type": link_type, "lag": lag}})

    def op_item_create(self, op):
        data = op.get("item") or {}
        if not isinstance(data, dict):
            raise ApiError(400, "validation_error", "Неверный формат строки")
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
        extra = data.get("extra") if isinstance(data.get("extra"), dict) else None
        if extra and "msp" in extra and not self.trusted:
            # Идентификаторы MS Project из браузера (копия строки) не принимаются: иначе копия выглядела бы строкой
            # файла — её нашло бы обновление из MS Project вместо исходной или удалило бы как «нет в файле»
            extra = {k: v for k, v in extra.items() if k != "msp"} or None
        item = PlanItem(plan_id=self.plan.id, key=key, type=item_type, name="",
                        created_at=self.now, created_by_id=self.user.id if self.user else None,
                        comment_count=0, extra=extra)
        defaults = {"task": TASK_DEFAULTS, "milestone": MILESTONE_DEFAULTS, "group": GROUP_DEFAULTS}[item_type]
        for attr, value in defaults.items():
            setattr(item, attr, value)
        if "position" not in data:
            alive = [i.position for i in self.items.values() if i.deleted_at is None]
            item.position = (max(alive) + 1024) if alive else 1024
        fields, legacy = self._split_legacy({k: v for k, v in data.items() if k not in ("key", "type", "extra")})
        self._set_item_fields(item, fields, creating=True)
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
        if legacy.get("dependencyKey"):
            # Предшественник может идти в пакете позже строки (документ, скрипт) — связь создаётся в конце пакета
            self.legacy_links.append((key, legacy))

    def _update_msp(self, item, extra):
        """Идентификаторы строки в MS Project (`extra.msp`: UID, GUID, WBS) — обновляются только из файла MS Project;
        остальное содержимое `extra` (служебные поля переноса 1.0) не трогается."""
        if not isinstance(extra, dict) or not isinstance(extra.get("msp"), dict):
            return False
        raw = extra["msp"]
        msp = {}
        if isinstance(raw.get("uid"), int) and not isinstance(raw.get("uid"), bool):
            msp["uid"] = raw["uid"]
        for name in ("guid", "wbs", "outlineNumber"):
            if isinstance(raw.get(name), str) and raw[name].strip():
                msp[name] = raw[name].strip()[:64]
        current = dict(item.extra or {})
        if current.get("msp") == msp:
            return False
        current["msp"] = msp
        item.extra = current
        return True

    def op_item_update(self, op):
        key = op.get("key")
        item = self._alive(self.items, key)
        if item is None:
            raise ApiError(409, "gone", "Строка удалена другим сотрудником", details={"key": key})
        fields = op.get("fields") or {}
        if not isinstance(fields, dict):
            raise ApiError(400, "validation_error", "Неверный формат изменений")
        fields, legacy = self._split_legacy(fields)
        if legacy:
            self._legacy_dependency(item, legacy)
        self._conflict(item, "item")
        before_name = self._label(item)
        extra = fields.pop("extra", None)
        extra_changed = self._update_msp(item, extra) if self.trusted else False
        changes = self._set_item_fields(item, fields)
        if extra_changed and not changes:
            # Изменились только идентификаторы строки в MS Project (обновление из файла) — без записи в истории
            self._touch(item, self.result.items)
            return
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
        if "constraintType" in changes or "constraintDate" in changes:
            def constraint(kind, day):
                # «Начало не ранее 10.06.2026» — тип и дата ограничения одной записью
                if not kind:
                    return "—"
                return CONSTRAINT_LABELS.get(kind, kind) + (f" {fmt_date(day)}" if day else "")
            old_kind = changes["constraintType"][0] if "constraintType" in changes else item.constraint_type
            old_day = changes["constraintDate"][0] if "constraintDate" in changes else item.constraint_date
            parts.append(f"Ограничение: {constraint(old_kind, old_day)} → "
                         f"{constraint(item.constraint_type, item.constraint_date)}")
        for api_name, (old, new) in changes.items():
            if api_name in ("start", "end", "position", "constraintType", "constraintDate"):
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
        self.restored[key] = item.deleted_at
        item.deleted_at = None
        item.deleted_by_id = None
        self._touch(item, self.result.items)
        self._event("item", key, "restore", f"{self._label(item)} восстановлена")

    # ---------- связи ----------
    def _graph_add(self, link):
        self._succ.setdefault(link.from_key, set()).add(link.to_key)
        self._pairs[(link.from_key, link.to_key)] = link

    def _graph_remove(self, link):
        if self._pairs.get((link.from_key, link.to_key)) is link:
            del self._pairs[(link.from_key, link.to_key)]
            self._succ.get(link.from_key, set()).discard(link.to_key)

    def _reaches(self, start, target):
        """Есть ли путь по живым связям от строки start к строке target."""
        stack, seen = [start], {start}
        while stack:
            node = stack.pop()
            if node == target:
                return True
            for successor in self._succ.get(node, ()):
                if successor not in seen:
                    seen.add(successor)
                    stack.append(successor)
        return False

    def _row_name(self, key):
        item = self.items.get(key)
        return f"«{(item.name or '').strip() or key}»" if item is not None else f"«{key}»"

    @staticmethod
    def _lag_text(lag, kind):
        unit = "раб. дн." if kind == "working" else "дн."
        return f"{lag:+d} {unit}" if lag else f"0 {unit}"

    def _link_label(self, link, details=True):
        """«Связь «A» → «B» (окончание → начало, +2 дн.)»."""
        text = f"Связь {self._row_name(link.from_key)} → {self._row_name(link.to_key)}"
        if details:
            parts = [LINK_TYPE_LABELS.get(link.type, link.type)]
            if link.lag:
                parts.append(self._lag_text(link.lag, link.lag_kind))
            text += f" ({', '.join(parts)})"
        return text

    def _cycle_message(self, link):
        return (f"Нельзя связать {self._row_name(link.from_key)} → {self._row_name(link.to_key)}: "
                f"{self._row_name(link.to_key)} уже предшествует {self._row_name(link.from_key)} "
                "(напрямую или через цепочку связей) — получился бы цикл")

    def _link_problem(self, link):
        """Почему связь не может быть живой: (статус, код, сообщение) или None. Цикл здесь не проверяется — его
        проверяет конец пакета (_check_cycles), один раз для всех связей пакета."""
        for role, key in (("Предшественник", link.from_key), ("Последующая строка", link.to_key)):
            item = self.items.get(key)
            if item is None:
                return 400, "validation_error", f"{role} связи не найден(а) в плане"
            if item.deleted_at is not None:
                return 409, "gone", f"{role} связи {self._row_name(key)} удален(а)"
            if item.type not in ("task", "milestone"):
                return 400, "validation_error", "Связь возможна только между задачами и вехами, группы не связываются"
        if link.from_key == link.to_key:
            return 400, "validation_error", "Строка не может быть предшественником самой себя"
        if self._nested(link.from_key, link.to_key) or self._nested(link.to_key, link.from_key):
            # Как в MS Project: вложенная задача и так в сроках родительской, связь между ними выталкивала бы её
            return 400, "validation_error", (f"Нельзя связать {self._row_name(link.from_key)} и "
                                             f"{self._row_name(link.to_key)}: одна строка вложена в другую")
        existing = self._pairs.get((link.from_key, link.to_key))
        if existing is not None and existing is not link:
            return 400, "validation_error", f"{self._link_label(link, details=False)} уже есть"
        return None

    def _nested(self, key, ancestor):
        """Строка key вложена (на любой глубине) в строку ancestor."""
        seen = {key}
        node = self.items.get(key)
        while node is not None and node.parent_key and node.parent_key not in seen:
            if node.parent_key == ancestor:
                return True
            seen.add(node.parent_key)
            node = self.items.get(node.parent_key)
        return False

    def _watch_cycle(self, link, strict, undo=None):
        """Связь стала живой или сменила концы: проверить на цикл в конце пакета. strict — цикл отклоняет пакет
        (правка сотрудника), иначе undo() отменяет только эту связь (файл, восстановление, отмена удаления)."""
        entry = self._cycle_checks.get(id(link))
        if entry is None:
            self._cycle_checks[id(link)] = [link, strict, undo]
        else:
            entry[1] = entry[1] or strict

    def _discard_link(self, link, event):
        """Убрать созданную в пакете связь совсем (связь из файла, замкнувшая цикл)."""
        state = sa_inspect(link)
        if state.persistent:
            db.session.delete(link)
        elif state.pending:
            db.session.expunge(link)
        self._graph_remove(link)
        self.links.pop(link.key, None)
        self.result.links.pop(link.key, None)
        if event in self.pending_events:
            self.pending_events.remove(event)

    def _graph_order_ok(self):
        """Граф живых связей без циклов — алгоритм Кана, один проход."""
        indegree = {}
        for source, targets in self._succ.items():
            indegree.setdefault(source, 0)
            for target in targets:
                indegree[target] = indegree.get(target, 0) + 1
        queue = [node for node, n in indegree.items() if n == 0]
        done = 0
        while queue:
            node = queue.pop()
            done += 1
            for target in self._succ.get(node, ()):
                indegree[target] -= 1
                if indegree[target] == 0:
                    queue.append(target)
        return done == len(indegree)

    def _cycle_core(self):
        """Строки, которые и достижимы из цикла, и ведут в цикл (Кан с обеих сторон): все циклы — внутри."""
        preds = {}
        for source, targets in self._succ.items():
            for target in targets:
                preds.setdefault(target, set()).add(source)

        def trim(forward, backward):
            nodes = set(forward) | set(backward)
            count = {node: len(backward.get(node, ())) for node in nodes}
            queue = [node for node, n in count.items() if n == 0]
            while queue:
                node = queue.pop()
                nodes.discard(node)
                for nxt in forward.get(node, ()):
                    count[nxt] -= 1
                    if count[nxt] == 0:
                        queue.append(nxt)
            return nodes

        return trim(self._succ, preds) & trim(preds, self._succ)

    def _check_cycles(self):
        """Цикл связей по итогам пакета. Без цикла — один проход по графу. С циклом связи пакета, лежащие в циклах,
        добавляются заново по одной в порядке операций: первая, замыкающая цикл, отклоняет пакет (правка сотрудника,
        сообщение о ней) или только отменяется (файл, восстановление)."""
        checks = [entry for entry in self._cycle_checks.values()
                  if entry[0].deleted_at is None and self.links.get(entry[0].key) is entry[0]]
        self._cycle_checks = {}
        if not checks or self._graph_order_ok():
            return
        core = self._cycle_core()
        suspects = [entry for entry in checks if entry[0].from_key in core and entry[0].to_key in core]
        for link, _strict, _undo in suspects:
            self._graph_remove(link)
        for link, strict, undo in suspects:
            if not self._reaches(link.to_key, link.from_key):
                self._graph_add(link)
                continue
            if strict or undo is None:
                raise ApiError(400, "link_cycle", self._cycle_message(link), details={"key": link.key})
            undo(self._cycle_message(link))

    def _check_link(self, link):
        problem = self._link_problem(link)
        if problem:
            status, code, message = problem
            raise ApiError(status, code, message, details={"key": link.key})

    def _link_fields(self, link, fields):
        changes = {}
        for name, value in fields.items():
            if name in ("from", "to"):
                new, attr = check_key(value, "ключ строки связи"), f"{name}_key"
            elif name == "type":
                if value not in LINK_TYPES:
                    raise ApiError(400, "validation_error", "Неизвестный тип связи")
                new, attr = value, "type"
            elif name == "lag":
                if value in (None, ""):
                    value = 0
                if not isinstance(value, int) or isinstance(value, bool) or abs(value) > MAX_LAG:
                    raise ApiError(400, "validation_error", "Лаг связи должен быть целым числом дней")
                new, attr = value, "lag"
            elif name == "lagKind":
                if value not in LAG_KIND_LABELS:
                    raise ApiError(400, "validation_error", "Лаг связи — в рабочих или календарных днях")
                new, attr = value, "lag_kind"
            else:
                continue
            old = getattr(link, attr)
            if old != new:
                setattr(link, attr, new)
                changes[name] = [old, new]
        return changes

    def op_link_create(self, op):
        self._create_link(op, strict=not self.skip_invalid_links)

    def _create_link(self, op, strict, cycle_fix=None):
        """Создать связь. strict — связь, замкнувшая цикл, отклоняет пакет; иначе связь не создаётся, а в fixes —
        отметка (cycle_fix — своя отметка вместо стандартной)."""
        data = op.get("link") or {}
        if not isinstance(data, dict):
            raise ApiError(400, "validation_error", "Неверный формат связи")
        key = data.get("key") or new_key("l")
        check_key(key)
        if key in self.links:
            if self._replayed_create(self.links, key, data, self.op_link_update):
                return
            raise ApiError(409, "key_exists", "Связь с таким ключом уже есть")
        if not data.get("from") or not data.get("to"):
            raise ApiError(400, "validation_error", "Укажите предшественника и последующую строку связи")
        link = PlanLink(plan_id=self.plan.id, key=key, type="FS", lag=0, lag_kind="calendar",
                        created_at=self.now, created_by_id=self.user.id if self.user else None)
        self._link_fields(link, {k: v for k, v in data.items() if k != "key"})
        problem = self._link_problem(link)
        if problem and self.skip_invalid_links:
            self._fix(link, "link", "key", f"связь не перенесена: {problem[2]}")
            return
        self._check_link(link)
        db.session.add(link)
        self.links[key] = link
        self._graph_add(link)
        self._touch(link, self.result.links)
        # Связь — в истории последующей строки: её сроки задаёт связь
        event = self._event("link", link.to_key, "create", f"{self._link_label(link)} добавлена",
                            {"link": key, "from": link.from_key, "type": link.type, "lag": link.lag,
                             "lagKind": link.lag_kind})

        def undo(message):
            self._discard_link(link, event)
            if cycle_fix is not None:
                cycle_fix()
            else:
                self._fix(link, "link", "key", f"связь не перенесена: {message}")

        self._watch_cycle(link, strict=strict, undo=undo)

    def op_link_update(self, op):
        link = self._alive(self.links, op.get("key"))
        if link is None:
            raise ApiError(409, "gone", "Связь удалена другим сотрудником", details={"key": op.get("key")})
        fields = op.get("fields") or {}
        if not isinstance(fields, dict):
            raise ApiError(400, "validation_error", "Неверный формат изменений")
        self._conflict(link, "link")
        before = self._link_label(link, details=False)
        self._graph_remove(link)
        changes = self._link_fields(link, fields)
        if changes:
            self._check_link(link)
        self._graph_add(link)
        if not changes:
            return
        if "from" in changes or "to" in changes:
            self._watch_cycle(link, strict=True)
        self._touch(link, self.result.links)
        parts = []
        for name, label in (("from", "предшественник"), ("to", "последующая")):
            if name in changes:
                parts.append(f"{label} {self._row_name(changes[name][0])} → {self._row_name(changes[name][1])}")
        if "type" in changes:
            old, new = changes["type"]
            parts.append(f"тип «{LINK_TYPE_LABELS[old]}» → «{LINK_TYPE_LABELS[new]}»")
        if "lag" in changes or "lagKind" in changes:
            old_lag = changes["lag"][0] if "lag" in changes else link.lag
            old_kind = changes["lagKind"][0] if "lagKind" in changes else link.lag_kind
            parts.append(f"лаг {self._lag_text(old_lag, old_kind)} → {self._lag_text(link.lag, link.lag_kind)}")
        self._event("link", link.to_key, "update", f"{before}: " + "; ".join(parts), {"link": link.key, **changes})

    def op_link_delete(self, op):
        link = self._alive(self.links, op.get("key"))
        if link is None:
            return
        link.deleted_at = self.now
        link.deleted_by_id = self.user.id if self.user else None
        self._graph_remove(link)
        self._touch(link, self.result.links)
        self._event("link", link.to_key, "delete", f"{self._link_label(link)} удалена", {"link": link.key})

    def _restore_link(self, link, summary, report=False):
        """Вернуть удалённую связь, если её строки живы и она не повторяет другую; True — вернули. Если в конце пакета
        окажется, что она замкнула цикл, связь снова удаляется (report — с отметкой в fixes)."""
        if self._link_problem(link) is not None:
            return False
        was = (link.deleted_at, link.deleted_by_id)
        link.deleted_at = None
        link.deleted_by_id = None
        self._graph_add(link)
        self._touch(link, self.result.links)
        event = self._event("link", link.to_key, "restore", summary, {"link": link.key})

        def undo(message):
            link.deleted_at, link.deleted_by_id = was
            self._graph_remove(link)
            if event in self.pending_events:
                self.pending_events.remove(event)
            if report:
                self._fix(link, "link", "key", f"связь не восстановлена: {message}")

        self._watch_cycle(link, strict=False, undo=undo)
        return True

    def op_link_restore(self, op):
        link = self.links.get(op.get("key"))
        if link is None or link.deleted_at is None:
            return
        if not self._restore_link(link, f"{self._link_label(link)} восстановлена", report=True):
            # Связь, которую уже нельзя вернуть (строку удалили, появилась такая же связь), остаётся удалённой:
            # отмена удаления строк из-за неё не отклоняется целиком, а клиент получает связь удалённой
            self._fix(link, "link", "key", f"связь не восстановлена: {self._link_problem(link)[2]}")
            self._touch(link, self.result.links)

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

    def _apply_legacy_links(self):
        """Предшественники из полей item.create до 0.7.0 — связями, когда все строки пакета уже созданы. Как и
        раньше, ссылка на отсутствующую строку, группу или цикл не отклоняет пакет, а снимается (fixes)."""
        for key, legacy in self.legacy_links:
            item = self._alive(self.items, key)
            if item is None:
                continue
            link = PlanLink(plan_id=self.plan.id, key=new_key("l"), from_key=legacy["dependencyKey"], to_key=key,
                            type=legacy.get("dependencyType") or "FS", lag=legacy.get("dependencyLag") or 0,
                            lag_kind="calendar")
            if self._link_problem(link) is not None:
                self._fix(item, "item", "dependencyKey", "предшественник удалён или недопустим")
                continue
            # Как и раньше, цикл через поле строки не отклоняет пакет, а снимает связь
            self._create_link({"link": {"key": link.key, "from": link.from_key, "to": key, "type": link.type,
                                        "lag": link.lag}}, strict=False,
                              cycle_fix=lambda item=item: self._fix(item, "item", "dependencyKey", "циклическая связь"))
        self.legacy_links = []

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
        # Связь удалённой строки удаляется вместе с ней (клиент обычно присылает link.delete сам)
        for link in self.links.values():
            if link.deleted_at is not None:
                continue
            ends = (alive_items.get(link.from_key), alive_items.get(link.to_key))
            if any(end is None or end.type == "group" for end in ends):
                link.deleted_at = self.now
                self._graph_remove(link)
                self._fix(link, "link", "from" if ends[0] is None or ends[0].type == "group" else "to",
                          "строка связи удалена")
                self._touch(link, self.result.links)
        # …и возвращается при её восстановлении: связи, удалённые не раньше строки (вместе с ней или вслед за
        # другой своей строкой), если обе строки живы и связь не даёт повтора или цикла. Восстановление версии плана
        # (revive_links=False) берёт связи только из версии
        if self.restored and self.revive_links:
            candidates = sorted((link for link in self.links.values() if link.deleted_at is not None
                                 and (link.from_key in self.restored or link.to_key in self.restored)),
                                key=lambda link: (link.id or 0, link.key))
            for link in candidates:
                for end in (link.from_key, link.to_key):
                    since = self.restored.get(end)
                    if since is not None and end in alive_items and link.deleted_at >= since:
                        self._restore_link(link, f"{self._link_label(link)} восстановлена вместе со строкой")
                        break
        for note in self.notes.values():
            if note.deleted_at is None and note.row_key is not None and note.row_key not in alive_items:
                # Пометка строки, которой больше нет, удаляется вместе со строкой (как в 1.0.x)
                note.deleted_at = self.now
                self._fix(note, "note", "rowKey", "строка удалена")
                self._touch(note, self.result.notes)

    # ---------- применение ----------
    def apply(self, ops, max_ops=MAX_OPS):
        """max_ops=None — без ограничения: пакет собирает сервер (копия плана, импорт MS Project), а не браузер."""
        if not isinstance(ops, list) or not ops:
            raise ApiError(400, "validation_error", "Нет операций")
        if max_ops is not None and len(ops) > max_ops:
            raise ApiError(400, "validation_error", "Слишком много операций в одном пакете")
        handlers = {
            "item.create": self.op_item_create, "item.update": self.op_item_update,
            "item.delete": self.op_item_delete, "item.restore": self.op_item_restore,
            "note.create": self.op_note_create, "note.update": self.op_note_update,
            "note.delete": self.op_note_delete, "note.restore": self.op_note_restore,
            "assignee.create": self.op_assignee_create, "assignee.update": self.op_assignee_update,
            "assignee.delete": self.op_assignee_delete, "assignee.restore": self.op_assignee_restore,
            "link.create": self.op_link_create, "link.update": self.op_link_update,
            "link.delete": self.op_link_delete, "link.restore": self.op_link_restore,
            "plan.update": self.op_plan_update,
        }
        for op in ops:
            if not isinstance(op, dict) or op.get("op") not in handlers:
                raise ApiError(400, "validation_error", f"Неизвестная операция: {op.get('op') if isinstance(op, dict) else op}")
            handlers[op["op"]](op)
        self._apply_legacy_links()
        self.check_integrity()
        self._check_cycles()
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


def apply_ops(plan, user, ops, base_revision=None, snapshot=True, audit_summary=True, project=None,
              restoring=False):
    """Применить пакет; вернуть OpsResult. План должен быть заблокирован `lock_plan`.

    restoring — пакет восстановления версии плана, его собирает сервер: без ограничения числа операций (версия
    большого плана после замены содержимого), с идентификаторами MS Project строк и без возврата связей
    восстановленных строк сверх тех, что есть в версии."""
    if snapshot:
        maybe_auto_snapshot(plan, user, current_app.config.get("SNAPSHOT_INTERVAL_MINUTES", 30))
    editor = PlanEditor(plan, user, base_revision, trusted=restoring, revive_links=not restoring)
    events = editor.apply(ops, max_ops=None if restoring else MAX_OPS)
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
