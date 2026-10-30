"""Структура компании: подразделения, руководители, место сотрудника (ADR-0008).

Revision ID: 0003_org_structure
Revises: 0002_jira_issues
Create Date: 2026-10-30
"""
from alembic import op
import sqlalchemy as sa


revision = '0003_org_structure'
down_revision = '0002_jira_issues'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('departments',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('parent_id', sa.String(length=36), nullable=True),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.Column('org_source', sa.String(length=20), nullable=False),
    sa.Column('external_id', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['parent_id'], ['departments.id'], name=op.f('fk_departments_parent_id_departments'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_departments'))
    )
    with op.batch_alter_table('departments', schema=None) as batch_op:
        batch_op.create_index('ix_departments_parent_name', ['parent_id', 'name'], unique=False)
        batch_op.create_index(batch_op.f('ix_departments_external_id'), ['external_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_departments_parent_id'), ['parent_id'], unique=False)

    op.create_table('department_heads',
    sa.Column('department_id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('is_primary', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['department_id'], ['departments.id'], name=op.f('fk_department_heads_department_id_departments'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_department_heads_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('department_id', 'user_id', name=op.f('pk_department_heads'))
    )
    with op.batch_alter_table('department_heads', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_department_heads_user_id'), ['user_id'], unique=False)

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('department_id', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('manager_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('reports_to_none', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('org_source', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('external_id', sa.String(length=255), nullable=True))
        batch_op.create_index(batch_op.f('ix_users_department_id'), ['department_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_users_manager_id'), ['manager_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_users_external_id'), ['external_id'], unique=False)
        batch_op.create_foreign_key(batch_op.f('fk_users_department_id_departments'), 'departments', ['department_id'], ['id'], ondelete='SET NULL')
        batch_op.create_foreign_key(batch_op.f('fk_users_manager_id_users'), 'users', ['manager_id'], ['id'], ondelete='SET NULL')


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_users_manager_id_users'), type_='foreignkey')
        batch_op.drop_constraint(batch_op.f('fk_users_department_id_departments'), type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_users_external_id'))
        batch_op.drop_index(batch_op.f('ix_users_manager_id'))
        batch_op.drop_index(batch_op.f('ix_users_department_id'))
        batch_op.drop_column('external_id')
        batch_op.drop_column('org_source')
        batch_op.drop_column('reports_to_none')
        batch_op.drop_column('manager_id')
        batch_op.drop_column('department_id')
    op.drop_table('department_heads')
    op.drop_table('departments')
