"""Кэш задач Jira, привязанных к строкам планов (ADR-0007).

Revision ID: 0002_jira_issues
Revises: 0001_initial
Create Date: 2026-10-30
"""
from alembic import op
import sqlalchemy as sa


revision = '0002_jira_issues'
down_revision = '0001_initial'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('jira_issues',
    sa.Column('key', sa.String(length=100), nullable=False),
    sa.Column('found', sa.Boolean(), nullable=False),
    sa.Column('data', sa.JSON(), nullable=True),
    sa.Column('fetched_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('key', name=op.f('pk_jira_issues'))
    )


def downgrade():
    op.drop_table('jira_issues')
