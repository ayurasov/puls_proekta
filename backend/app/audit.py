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


def _text(value):
    if value is None:
        return ""
    return str(value)[:MAX_VALUE]


def record(section, action, entity, field="", old="", new="", project=None, details=None, user=None):
    user = user or current_user()
    db.session.add(AuditLog(
        user_id=user.id if user else None,
        user_name=(user.login if user else "система"),
        project_id=project.id if project is not None else None,
        project_title=project.title if project is not None else "",
        section=section,
        action=action,
        entity=_text(entity),
        field=field or "",
        old_value=_text(old),
        new_value=_text(new),
        details=details,
    ))
