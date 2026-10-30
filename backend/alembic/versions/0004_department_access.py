"""Доступ подразделений к проектам и направлениям (ADR-0008).

Revision ID: 0004_department_access
Revises: 0003_org_structure
Create Date: 2026-10-30
"""
from alembic import op
import sqlalchemy as sa


revision = '0004_department_access'
down_revision = '0003_org_structure'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('department_access_rules',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('department_id', sa.String(length=36), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=True),
    sa.Column('direction_id', sa.Integer(), nullable=True),
    sa.Column('level', sa.String(length=10), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.CheckConstraint("(project_id is null and direction_id is not null) or (project_id is not null and direction_id is null)", name=op.f('ck_department_access_rules_target')),
    sa.CheckConstraint("level in ('view','edit','manage')", name=op.f('ck_department_access_rules_level')),
    sa.ForeignKeyConstraint(['department_id'], ['departments.id'], name=op.f('fk_department_access_rules_department_id_departments'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['direction_id'], ['directions.id'], name=op.f('fk_department_access_rules_direction_id_directions'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_department_access_rules_project_id_projects'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_department_access_rules'))
    )
    with op.batch_alter_table('department_access_rules', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_department_access_rules_department_id'), ['department_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_department_access_rules_direction_id'), ['direction_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_department_access_rules_project_id'), ['project_id'], unique=False)
        batch_op.create_index('uq_department_access_direction', ['department_id', 'direction_id'], unique=True, sqlite_where=sa.text('direction_id is not null'), postgresql_where=sa.text('direction_id is not null'))
        batch_op.create_index('uq_department_access_project', ['department_id', 'project_id'], unique=True, sqlite_where=sa.text('project_id is not null'), postgresql_where=sa.text('project_id is not null'))


def downgrade():
    op.drop_table('department_access_rules')
