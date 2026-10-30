"""Реестр изменений (журнал администратора).

Разделы (section) совпадают с 1.0.x: «Гант», «Проекты», «Пользователи», плюс новые
«Направления», «Доступы», «Настройки», «Вход». В отличие от 1.0.x, изменения плана
записываются с содержанием («Статус задачи «…»: Новая → В работе»), а не одной строкой
«(данные обновлены)» на каждое автосохранение.
"""

from app.extensions import db
from app.models import AuditLog
from app.security import current_user

MAX_VALUE = 1000


def _text(value, limit=MAX_VALUE):
    if value is None:
        return ""
    return str(value)[:limit]


def record(section, action, entity, field="", old="", new="", project=None, details=None, user=None):
    user = user or current_user()
    db.session.add(AuditLog(
        user_id=user.id if user else None,
        user_name=_text(user.login if user else "система", 255),
        project_id=project.id if project is not None else None,
        project_title=_text(project.title if project is not None else "", 500),
        section=_text(section, 100),
        action=_text(action, 30),
        # Размеры — по столбцам audit_log: в PostgreSQL длиннее нельзя (SQLite длину не проверяет)
        entity=_text(entity, 500),
        field=_text(field, 100),
        old_value=_text(old),
        new_value=_text(new),
        details=details,
    ))
