"""Полномочия без роли администратора (ADR-0013): users.capabilities — список ключей.

Администратор выдаёт сотруднику отдельные полномочия («Смотрит состояние системы», «Настраивает модули»,
«Ведёт учётные записи» и др.), не делая его администратором. У существующих сотрудников список пуст —
права не меняются. Откат удаляет столбец.

Revision ID: 0008_user_capabilities
Revises: 0007_plan_links
Create Date: 2026-11-05
"""
from alembic import op
import sqlalchemy as sa


revision = '0008_user_capabilities'
down_revision = '0007_plan_links'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users') as batch:
        batch.add_column(sa.Column('capabilities', sa.JSON(), nullable=False, server_default='[]'))


def downgrade():
    with op.batch_alter_table('users') as batch:
        batch.drop_column('capabilities')
