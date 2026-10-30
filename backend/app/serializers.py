"""Преобразование моделей в JSON API (camelCase)."""


def iso(value):
    if value is None:
        return None
    if hasattr(value, "hour"):
        return value.replace(microsecond=0).isoformat() + "Z"
    return value.isoformat()


def user_public(user):
    return {
        "id": user.id,
        "login": user.login,
        "name": user.name,
        "position": user.position or "",
        "avatarUrl": avatar_url(user),
    }


def avatar_url(user):
    if not user.avatar_version:
        return None
    return f"/api/users/{user.id}/avatar?v={user.avatar_version}"


def user_full(user):
    return {
        **user_public(user),
        "email": user.email or "",
        "role": user.role,
        "isActive": bool(user.is_active),
        "authSource": user.auth_source,
        "createdAt": iso(user.created_at),
        "updatedAt": iso(user.updated_at),
        "lastLoginAt": iso(user.last_login_at),
        "departmentId": user.department_id,
        "managerId": user.manager_id,
        "reportsToNone": bool(user.reports_to_none),
        "externalId": user.external_id,
        "orgSource": user.org_source,
    }


def direction(d):
    return {
        "id": d.id,
        "name": d.name,
        "description": d.description or "",
        "color": d.color or "blue",
        "position": d.position,
    }
