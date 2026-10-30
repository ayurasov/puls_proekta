"""Связь с «По Делу»: идентификаторы сотрудников и подразделений в «По Делу» (ADR-0009).

Только добавление столбцов (без изменения существующих данных): ссылка на карточку в «По Делу»
и сопоставление сотрудника при повторной синхронизации.

Revision ID: 0005_podelu_links
Revises: 0004_department_access
Create Date: 2026-10-30
"""
from alembic import op
import sqlalchemy as sa


revision = '0005_podelu_links'
down_revision = '0004_department_access'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('podelu_id', sa.String(length=36), nullable=True))
        batch_op.create_index(batch_op.f('ix_users_podelu_id'), ['podelu_id'], unique=False)

    with op.batch_alter_table('departments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('podelu_id', sa.String(length=36), nullable=True))
        batch_op.create_index(batch_op.f('ix_departments_podelu_id'), ['podelu_id'], unique=False)


def downgrade():
    with op.batch_alter_table('departments', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_departments_podelu_id'))
        batch_op.drop_column('podelu_id')

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_podelu_id'))
        batch_op.drop_column('podelu_id')
