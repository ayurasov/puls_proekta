"""ORM-модели «Пульса Проекта».

Модель данных (ADR-0002):

* сотрудники, их фото и серверные сессии;
* направления (группы проектов) и проекты;
* планы — бывшие «вкладки-версии» диаграммы Ганта 1.0.x: у проекта их может быть
  несколько, закрытая вкладка становится архивным планом, а не исчезает;
* строки плана (задачи, вехи, группы), пометки, исполнители плана, комментарии;
* журнал изменений плана (кто, когда, что было и что стало) и снимки плана для отката;
* правила доступа, реестр изменений (аудит), настройки, производственный календарь.

Все отметки времени — наивный UTC (`utcnow()`), API отдаёт их в ISO 8601 с `Z`.
Строки, пометки и исполнители удаляются «мягко» (`deleted_at`): удаление можно отменить,
а открытые у коллег планы узнают об удалении при очередной синхронизации.
"""

from datetime import datetime, timezone

from app.extensions import db


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


ROLES = ("admin", "manager", "editor", "viewer")
ACCESS_LEVELS = ("view", "edit", "manage")
ITEM_TYPES = ("task", "milestone", "group")
TASK_STATUSES = ("new", "inwork", "blocked", "done")
DEPENDENCY_TYPES = ("FS", "SS", "FF", "SF")


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    login = db.Column(db.String(150), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=True)
    name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), nullable=False, default="")
    position = db.Column(db.String(500), nullable=False, default="")
    role = db.Column(db.String(20), nullable=False, default="viewer")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    auth_source = db.Column(db.String(20), nullable=False, default="local")
    avatar_version = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    last_login_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.CheckConstraint("role in ('admin','manager','editor','viewer')", name="role"),
        db.CheckConstraint("auth_source in ('local','ldap')", name="auth_source"),
    )


class UserAvatar(db.Model):
    __tablename__ = "user_avatars"

    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    content_type = db.Column(db.String(100), nullable=False)
    data = db.Column(db.LargeBinary, nullable=False)
    size = db.Column(db.Integer, nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class UserSession(db.Model):
    """Серверная сессия: в cookie — случайный токен, в БД — его SHA-256."""

    __tablename__ = "user_sessions"

    id = db.Column(db.String(64), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    data = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    last_seen_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    ip = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.String(300), nullable=True)


class Direction(db.Model):
    """Направление — группа проектов на главной странице (в 1.0.x — project_groups)."""

    __tablename__ = "directions"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=False, default="")
    color = db.Column(db.String(32), nullable=False, default="blue")
    position = db.Column(db.Float, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class Project(db.Model):
    __tablename__ = "projects"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(500), nullable=False)
    subtitle = db.Column(db.String(1000), nullable=False, default="")
    direction_id = db.Column(db.Integer, db.ForeignKey("directions.id", ondelete="SET NULL"), nullable=True, index=True)
    position = db.Column(db.Float, nullable=False, default=0)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    deleted_at = db.Column(db.DateTime, nullable=True, index=True)
    deleted_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    plans = db.relationship("Plan", back_populates="project", cascade="all, delete-orphan", passive_deletes=True)


class Plan(db.Model):
    """План проекта — одна диаграмма Ганта (бывшая вкладка-версия 1.0.x)."""

    __tablename__ = "plans"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    key = db.Column(db.String(64), nullable=False, unique=True)
    title = db.Column(db.String(500), nullable=False)
    subtitle = db.Column(db.String(1000), nullable=False, default="")
    position = db.Column(db.Float, nullable=False, default=0)
    settings = db.Column(db.JSON, nullable=False, default=dict)
    revision = db.Column(db.Integer, nullable=False, default=0)
    meta_rev = db.Column(db.Integer, nullable=False, default=0)
    next_row_seq = db.Column(db.Integer, nullable=False, default=1)
    is_archived = db.Column(db.Boolean, nullable=False, default=False)
    archived_at = db.Column(db.DateTime, nullable=True)
    archived_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    last_snapshot_at = db.Column(db.DateTime, nullable=True)
    deleted_at = db.Column(db.DateTime, nullable=True)
    deleted_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    project = db.relationship("Project", back_populates="plans")


class _PlanEntityMixin:
    """Общие поля сущностей плана: ключ внутри плана, метки изменений, мягкое удаление."""

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(64), nullable=False)
    extra = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    created_by_id = db.Column(db.Integer, nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_by_id = db.Column(db.Integer, nullable=True)
    updated_rev = db.Column(db.Integer, nullable=False, default=0)
    deleted_at = db.Column(db.DateTime, nullable=True)
    deleted_by_id = db.Column(db.Integer, nullable=True)


class PlanItem(_PlanEntityMixin, db.Model):
    """Строка плана: задача, веха или группа."""

    __tablename__ = "plan_items"

    plan_id = db.Column(db.Integer, db.ForeignKey("plans.id", ondelete="CASCADE"), nullable=False, index=True)
    type = db.Column(db.String(12), nullable=False)
    name = db.Column(db.Text, nullable=False, default="")
    parent_key = db.Column(db.String(64), nullable=True)
    position = db.Column(db.Float, nullable=False, default=0)
    status = db.Column(db.String(12), nullable=True)
    start_date = db.Column(db.Date, nullable=True)
    end_date = db.Column(db.Date, nullable=True)
    end_mode = db.Column(db.String(10), nullable=True)
    duration = db.Column(db.Integer, nullable=True)
    duration_type = db.Column(db.String(10), nullable=True)
    progress = db.Column(db.Integer, nullable=True)
    offset_days = db.Column(db.Integer, nullable=True)
    completed_date = db.Column(db.Date, nullable=True)
    dependency_key = db.Column(db.String(64), nullable=True)
    dependency_type = db.Column(db.String(2), nullable=True)
    dependency_lag = db.Column(db.Integer, nullable=True)
    assignee_key = db.Column(db.String(64), nullable=True)
    category = db.Column(db.String(255), nullable=True)
    color = db.Column(db.String(32), nullable=True)
    bar_text = db.Column(db.String(500), nullable=True)
    jira_url = db.Column(db.String(2000), nullable=True)
    comment = db.Column(db.Text, nullable=True)
    line_style = db.Column(db.String(10), nullable=True)
    line_color = db.Column(db.String(32), nullable=True)
    comment_count = db.Column(db.Integer, nullable=False, default=0)

    __table_args__ = (
        db.UniqueConstraint("plan_id", "key", name="uq_plan_items_plan_key"),
        db.CheckConstraint("type in ('task','milestone','group')", name="type"),
        db.Index("ix_plan_items_plan_rev", "plan_id", "updated_rev"),
    )


class PlanNote(_PlanEntityMixin, db.Model):
    """Пометка на шкале (булавка с текстом) — ко всему графику или к строке."""

    __tablename__ = "plan_notes"

    plan_id = db.Column(db.Integer, db.ForeignKey("plans.id", ondelete="CASCADE"), nullable=False, index=True)
    date = db.Column(db.Date, nullable=False)
    text = db.Column(db.Text, nullable=False, default="")
    row_key = db.Column(db.String(64), nullable=True)
    color = db.Column(db.String(32), nullable=True)
    show_date = db.Column(db.Boolean, nullable=False, default=False)

    __table_args__ = (db.UniqueConstraint("plan_id", "key", name="uq_plan_notes_plan_key"),)


class PlanAssignee(_PlanEntityMixin, db.Model):
    """Исполнитель плана (справочник плана); может быть связан с сотрудником системы."""

    __tablename__ = "plan_assignees"

    plan_id = db.Column(db.Integer, db.ForeignKey("plans.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(500), nullable=False)
    role = db.Column(db.String(500), nullable=False, default="")
    color = db.Column(db.String(32), nullable=False, default="#2563eb")
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    position = db.Column(db.Float, nullable=False, default=0)

    __table_args__ = (db.UniqueConstraint("plan_id", "key", name="uq_plan_assignees_plan_key"),)


class ItemComment(db.Model):
    __tablename__ = "item_comments"

    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(db.Integer, db.ForeignKey("plans.id", ondelete="CASCADE"), nullable=False)
    item_key = db.Column(db.String(64), nullable=False)
    author_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    author_name = db.Column(db.String(255), nullable=False, default="")
    text = db.Column(db.Text, nullable=False)
    reply_to_id = db.Column(db.Integer, db.ForeignKey("item_comments.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=True)
    deleted_at = db.Column(db.DateTime, nullable=True)
    deleted_by_id = db.Column(db.Integer, nullable=True)
    extra = db.Column(db.JSON, nullable=True)

    __table_args__ = (db.Index("ix_item_comments_plan_item", "plan_id", "item_key"),)


class PlanEvent(db.Model):
    """Запись журнала плана: одно изменение одной сущности (или служебное событие)."""

    __tablename__ = "plan_events"

    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(db.Integer, db.ForeignKey("plans.id", ondelete="CASCADE"), nullable=False)
    revision = db.Column(db.Integer, nullable=False, default=0)
    entity = db.Column(db.String(20), nullable=False)
    entity_key = db.Column(db.String(64), nullable=True)
    action = db.Column(db.String(30), nullable=False)
    user_id = db.Column(db.Integer, nullable=True)
    user_name = db.Column(db.String(255), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    summary = db.Column(db.Text, nullable=False, default="")
    changes = db.Column(db.JSON, nullable=True)

    __table_args__ = (
        db.Index("ix_plan_events_plan_id_id", "plan_id", "id"),
        db.Index("ix_plan_events_plan_entity", "plan_id", "entity_key"),
    )


class PlanSnapshot(db.Model):
    """Полный снимок плана для просмотра и отката (ADR-0002)."""

    __tablename__ = "plan_snapshots"

    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(db.Integer, db.ForeignKey("plans.id", ondelete="CASCADE"), nullable=False, index=True)
    revision = db.Column(db.Integer, nullable=False, default=0)
    reason = db.Column(db.String(30), nullable=False, default="auto")
    label = db.Column(db.String(500), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    created_by_id = db.Column(db.Integer, nullable=True)
    user_name = db.Column(db.String(255), nullable=False, default="")
    items_count = db.Column(db.Integer, nullable=False, default=0)
    data = db.Column(db.JSON, nullable=False)


class PlanPresence(db.Model):
    __tablename__ = "plan_presence"

    plan_id = db.Column(db.Integer, db.ForeignKey("plans.id", ondelete="CASCADE"), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    last_seen_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    is_editing = db.Column(db.Boolean, nullable=False, default=False)


class AccessRule(db.Model):
    """Доступ сотрудника к проекту или ко всем проектам направления."""

    __tablename__ = "access_rules"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)
    direction_id = db.Column(db.Integer, db.ForeignKey("directions.id", ondelete="CASCADE"), nullable=True, index=True)
    level = db.Column(db.String(10), nullable=False, default="view")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    created_by_id = db.Column(db.Integer, nullable=True)

    __table_args__ = (
        db.CheckConstraint("level in ('view','edit','manage')", name="level"),
        db.CheckConstraint(
            "(project_id is null and direction_id is not null) or (project_id is not null and direction_id is null)",
            name="target",
        ),
        db.Index("uq_access_rules_user_project", "user_id", "project_id", unique=True,
                 sqlite_where=db.text("project_id is not null"), postgresql_where=db.text("project_id is not null")),
        db.Index("uq_access_rules_user_direction", "user_id", "direction_id", unique=True,
                 sqlite_where=db.text("direction_id is not null"), postgresql_where=db.text("direction_id is not null")),
    )


class AuditLog(db.Model):
    """Реестр изменений для администратора. Записи 1.0.x перенесены без изменений."""

    __tablename__ = "audit_log"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    user_id = db.Column(db.Integer, nullable=True, index=True)
    user_name = db.Column(db.String(255), nullable=False, default="")
    project_id = db.Column(db.Integer, nullable=True, index=True)
    project_title = db.Column(db.String(500), nullable=False, default="")
    section = db.Column(db.String(100), nullable=False, default="")
    action = db.Column(db.String(30), nullable=False)
    entity = db.Column(db.String(500), nullable=False, default="")
    field = db.Column(db.String(100), nullable=False, default="")
    old_value = db.Column(db.Text, nullable=False, default="")
    new_value = db.Column(db.Text, nullable=False, default="")
    details = db.Column(db.JSON, nullable=True)


class AppSetting(db.Model):
    __tablename__ = "app_settings"

    key = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.JSON, nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class CalendarDay(db.Model):
    """Исключение производственного календаря: праздник/перенесённый выходной или рабочая суббота."""

    __tablename__ = "calendar_days"

    day = db.Column(db.Date, primary_key=True)
    kind = db.Column(db.String(10), nullable=False)
    title = db.Column(db.String(255), nullable=False, default="")
    source = db.Column(db.String(20), nullable=False, default="official")

    __table_args__ = (db.CheckConstraint("kind in ('holiday','workday')", name="kind"),)
