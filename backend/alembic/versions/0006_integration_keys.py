"""Ключи интеграций (ADR-0011): несколько ключей для API /api/integration/v1 с правами (scopes).

Таблица integration_keys. Ключ «Пульса» для «По Делу» (app_settings['podelu_links'].inbound — SHA-256, первые
символы, даты выпуска и последнего использования) переносится в таблицу с правом projects.read — «По Делу»
продолжает работать с тем же ключом. Откат возвращает действующий ключ «По Делу» в настройку и удаляет таблицу.

Revision ID: 0006_integration_keys
Revises: 0005_podelu_links
Create Date: 2026-11-01
"""
import json
from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision = '0006_integration_keys'
down_revision = '0005_podelu_links'
branch_labels = None
depends_on = None

SETTING = 'podelu_links'


def _parse(value):
    """ISO 8601 с «Z» → наивный UTC, как остальные отметки времени; иначе None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).replace(tzinfo=None)
    except ValueError:
        return None


def _iso(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.replace(microsecond=0).isoformat() + 'Z'


def _load(raw):
    if isinstance(raw, (bytes, str)):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    return raw if isinstance(raw, dict) else None


def _write(conn, sql, value):
    """Запись JSON-значения настройки: тип параметра — JSON (в PostgreSQL столбец json, строку он не примет)."""
    stmt = sa.text(sql).bindparams(sa.bindparam('value', type_=sa.JSON()))
    conn.execute(stmt, {'value': value, 'key': SETTING})


def upgrade():
    table = op.create_table(
        'integration_keys',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('app', sa.String(length=30), nullable=False),
        sa.Column('scopes', sa.JSON(), nullable=False),
        sa.Column('key_hash', sa.String(length=64), nullable=False),
        sa.Column('prefix', sa.String(length=12), nullable=False),
        sa.Column('issued_at', sa.DateTime(), nullable=False),
        sa.Column('issued_by_id', sa.Integer(), nullable=True),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.Column('revoked_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['issued_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['revoked_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key_hash'),
    )
    op.create_index('ix_integration_keys_revoked_at', 'integration_keys', ['revoked_at'], unique=False)

    # Ключ «По Делу» из настройки — в таблицу, с прежними правами (projects.read)
    conn = op.get_bind()
    row = conn.execute(sa.text('SELECT value FROM app_settings WHERE key = :key'), {'key': SETTING}).first()
    value = _load(row[0]) if row else None
    inbound = value.get('inbound') if value else None
    if isinstance(inbound, dict) and isinstance(inbound.get('hash'), str) and len(inbound['hash']) == 64:
        issued = _parse(inbound.get('createdAt')) or datetime.utcnow().replace(microsecond=0)
        conn.execute(table.insert().values(
            name='По Делу', app='podelu', scopes=['projects.read'], key_hash=inbound['hash'],
            prefix=str(inbound.get('prefix') or '')[:12], issued_at=issued, issued_by_id=None,
            last_used_at=_parse(inbound.get('lastUsedAt')), revoked_at=None, revoked_by_id=None, created_at=issued,
        ))
    if value is not None and 'inbound' in value:
        _write(conn, 'UPDATE app_settings SET value = :value WHERE key = :key',
               {k: v for k, v in value.items() if k != 'inbound'})


def downgrade():
    conn = op.get_bind()
    key = conn.execute(sa.text(
        "SELECT key_hash, prefix, issued_at, last_used_at FROM integration_keys "
        "WHERE app = 'podelu' AND revoked_at IS NULL ORDER BY issued_at DESC, id DESC")).first()
    if key is not None:
        row = conn.execute(sa.text('SELECT value FROM app_settings WHERE key = :key'), {'key': SETTING}).first()
        value = (_load(row[0]) if row else None) or {}
        value['inbound'] = {'hash': key[0], 'prefix': key[1], 'createdAt': _iso(key[2]), 'lastUsedAt': _iso(key[3])}
        if row:
            _write(conn, 'UPDATE app_settings SET value = :value WHERE key = :key', value)
        else:
            _write(conn, 'INSERT INTO app_settings (key, value, updated_at) VALUES (:key, :value, CURRENT_TIMESTAMP)',
                   value)
    op.drop_index('ix_integration_keys_revoked_at', table_name='integration_keys')
    op.drop_table('integration_keys')
