"""Фото сотрудников: приём, проверка, хранение в БД и отдача с кэшированием.

В 1.0.x фото хранилось строкой data:URL в таблице users и приходило в каждом ответе
/api/auth/me и /api/users (до 5 МБ JSON на список сотрудников). Теперь фото — отдельная
таблица и отдельный адрес с версией в URL: браузер кэширует его навсегда.
"""

import base64
import binascii
import hashlib

from flask import Blueprint, Response, request

from app.extensions import db
from app.models import User, UserAvatar, utcnow
from app.security import ApiError, login_required

bp = Blueprint("avatars", __name__)

_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def sniff_image(data):
    for signature, content_type in _SIGNATURES:
        if data.startswith(signature):
            return content_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def decode_data_url(value):
    """('image/jpeg', bytes) из data:image/...;base64,... или None."""
    if not isinstance(value, str) or not value.startswith("data:") or "," not in value:
        return None
    header, encoded = value.split(",", 1)
    if ";base64" not in header:
        return None
    try:
        data = base64.b64decode(encoded, validate=False)
    except (binascii.Error, ValueError):
        return None
    content_type = sniff_image(data)
    if content_type is None:
        return None
    return content_type, data


def read_avatar_upload(max_bytes):
    upload = request.files.get("file")
    if upload is not None:
        data = upload.read(max_bytes + 1)
    else:
        payload = request.get_json(silent=True) or {}
        decoded = decode_data_url(payload.get("dataUrl") or payload.get("avatar"))
        if decoded is None:
            raise ApiError(400, "invalid_image", "Нужно изображение PNG, JPEG, GIF или WebP")
        data = decoded[1]
    if not data:
        raise ApiError(400, "invalid_image", "Файл пустой")
    if len(data) > max_bytes:
        raise ApiError(400, "too_large", f"Файл слишком большой (не больше {max_bytes // (1024 * 1024)} МБ)")
    content_type = sniff_image(data)
    if content_type is None:
        raise ApiError(400, "invalid_image", "Нужно изображение PNG, JPEG, GIF или WebP")
    return content_type, data


def store_avatar(user, content_type, data):
    avatar = db.session.get(UserAvatar, user.id)
    if avatar is None:
        avatar = UserAvatar(user_id=user.id, content_type=content_type, data=data, size=len(data),
                            sha256=hashlib.sha256(data).hexdigest())
        db.session.add(avatar)
    else:
        avatar.content_type = content_type
        avatar.data = data
        avatar.size = len(data)
        avatar.sha256 = hashlib.sha256(data).hexdigest()
        avatar.updated_at = utcnow()
    user.avatar_version = (user.avatar_version or 0) + 1


@bp.get("/api/users/<int:user_id>/avatar")
@login_required
def get_avatar(user_id):
    user = db.session.get(User, user_id)
    avatar = db.session.get(UserAvatar, user_id) if user else None
    if avatar is None:
        raise ApiError(404, "not_found", "Фото нет")
    etag = avatar.sha256
    if request.if_none_match and etag in request.if_none_match:
        return Response(status=304, headers={"ETag": f'"{etag}"'})
    return Response(avatar.data, mimetype=avatar.content_type, headers={
        "ETag": f'"{etag}"',
        "Cache-Control": "private, max-age=31536000, immutable",
        "X-Content-Type-Options": "nosniff",
    })
