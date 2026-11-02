"""Связи плана — отдельная таблица (ADR-0012, 5.1); крайний срок и ограничение даты строки (5.4).

До 0.7.0 у строки был один предшественник — столбцы plan_items.dependency_key, dependency_type, dependency_lag.
Теперь связь — сущность plan_links: у задачи сколько угодно предшественников, последующей может быть и веха.

Перенос: предшественник каждой строки — живой и удалённой — становится связью с лагом в календарных днях
(ключ lm<id строки>: детерминированный и не совпадает со случайными ключами клиента l<hex>). Связь удалённой
строки сохраняется удалённой с той же отметкой времени — она вернётся, если строку восстановят. Ссылки на
отсутствующие строки, на группы и на саму себя не переносятся (как и раньше, они ничего не значили).
Старые столбцы остаются до следующего выпуска: приложение их не читает, откат заполняет их снова.

Откат: в dependency_* строки-задачи записывается её первая связь (живая; у удалённой строки — удалённая вместе
с ней); если все связи строки сняты в 0.7.0 — предшественник снимается; строки без связей остаются как были до
обновления. Лаг в рабочих днях записывается тем же числом. Таблица связей и новые столбцы удаляются.

Revision ID: 0007_plan_links
Revises: 0006_integration_keys
Create Date: 2026-11-02
"""
from alembic import op
import sqlalchemy as sa


revision = '0007_plan_links'
down_revision = '0006_integration_keys'
branch_labels = None
depends_on = None

TYPES = ('FS', 'SS', 'FF', 'SF')
CHUNK = 500

items_table = sa.table(
    'plan_items',
    sa.column('id', sa.Integer), sa.column('plan_id', sa.Integer), sa.column('key', sa.String),
    sa.column('type', sa.String), sa.column('dependency_key', sa.String), sa.column('dependency_type', sa.String),
    sa.column('dependency_lag', sa.Integer), sa.column('created_at', sa.DateTime),
    sa.column('created_by_id', sa.Integer), sa.column('updated_at', sa.DateTime),
    sa.column('updated_by_id', sa.Integer), sa.column('updated_rev', sa.Integer),
    sa.column('deleted_at', sa.DateTime), sa.column('deleted_by_id', sa.Integer),
)


def upgrade():
    links = op.create_table(
        'plan_links',
        sa.Column('plan_id', sa.Integer(), nullable=False),
        sa.Column('from_key', sa.String(length=64), nullable=False),
        sa.Column('to_key', sa.String(length=64), nullable=False),
        sa.Column('type', sa.String(length=2), nullable=False),
        sa.Column('lag', sa.Integer(), nullable=False),
        sa.Column('lag_kind', sa.String(length=10), nullable=False),
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=64), nullable=False),
        sa.Column('extra', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('updated_by_id', sa.Integer(), nullable=True),
        sa.Column('updated_rev', sa.Integer(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.Column('deleted_by_id', sa.Integer(), nullable=True),
        sa.CheckConstraint("type in ('FS','SS','FF','SF')", name=op.f('ck_plan_links_type')),
        sa.CheckConstraint("lag_kind in ('calendar','working')", name=op.f('ck_plan_links_lag_kind')),
        sa.ForeignKeyConstraint(['plan_id'], ['plans.id'], name=op.f('fk_plan_links_plan_id_plans'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_plan_links')),
        sa.UniqueConstraint('plan_id', 'key', name='uq_plan_links_plan_key'),
    )
    with op.batch_alter_table('plan_links', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_plan_links_plan_id'), ['plan_id'], unique=False)
        batch_op.create_index('ix_plan_links_plan_rev', ['plan_id', 'updated_rev'], unique=False)
    with op.batch_alter_table('plan_items', schema=None) as batch_op:
        batch_op.add_column(sa.Column('deadline', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('constraint_type', sa.String(length=8), nullable=True))
        batch_op.add_column(sa.Column('constraint_date', sa.Date(), nullable=True))

    conn = op.get_bind()
    rows = conn.execute(sa.select(
        items_table.c.id, items_table.c.plan_id, items_table.c.key, items_table.c.type, items_table.c.dependency_key,
        items_table.c.dependency_type, items_table.c.dependency_lag, items_table.c.created_at,
        items_table.c.created_by_id, items_table.c.updated_at, items_table.c.updated_by_id, items_table.c.updated_rev,
        items_table.c.deleted_at, items_table.c.deleted_by_id,
    ).order_by(items_table.c.id)).all()
    by_key = {(r.plan_id, r.key): r for r in rows}
    batch = []
    for row in rows:
        if not row.dependency_key or row.dependency_key == row.key or row.type == 'group':
            continue
        dep = by_key.get((row.plan_id, row.dependency_key))
        if dep is None or dep.type == 'group':
            continue
        # Связь удалена, если удалена любая из её строк, — с отметкой того удаления, что было позже
        ends = [r for r in (row, dep) if r.deleted_at is not None]
        last = max(ends, key=lambda r: r.deleted_at) if ends else None
        batch.append({
            'plan_id': row.plan_id, 'key': f'lm{row.id}', 'from_key': dep.key, 'to_key': row.key,
            'type': row.dependency_type if row.dependency_type in TYPES else 'FS', 'lag': row.dependency_lag or 0,
            'lag_kind': 'calendar', 'extra': None, 'created_at': row.updated_at or row.created_at,
            'created_by_id': row.updated_by_id, 'updated_at': row.updated_at or row.created_at,
            'updated_by_id': row.updated_by_id, 'updated_rev': row.updated_rev or 0,
            'deleted_at': last.deleted_at if last else None, 'deleted_by_id': last.deleted_by_id if last else None,
        })
        if len(batch) >= CHUNK:
            conn.execute(links.insert(), batch)
            batch = []
    if batch:
        conn.execute(links.insert(), batch)


def downgrade():
    conn = op.get_bind()
    links = sa.table(
        'plan_links', sa.column('id', sa.Integer), sa.column('plan_id', sa.Integer),
        sa.column('from_key', sa.String), sa.column('to_key', sa.String), sa.column('type', sa.String),
        sa.column('lag', sa.Integer), sa.column('deleted_at', sa.DateTime),
    )
    first = {}
    for link in conn.execute(sa.select(links).order_by(links.c.id)).all():
        first.setdefault((link.plan_id, link.to_key), []).append(link)
    items = conn.execute(sa.select(
        items_table.c.id, items_table.c.plan_id, items_table.c.key, items_table.c.type,
        items_table.c.dependency_key, items_table.c.deleted_at,
    )).all()
    for item in items:
        chosen = None
        if item.type == 'task':
            for link in first.get((item.plan_id, item.key), []):
                # У живой строки — живая связь; у удалённой — удалённая вместе с ней или позже
                if link.deleted_at is None or (item.deleted_at is not None and link.deleted_at >= item.deleted_at):
                    chosen = link
                    break
        if chosen is not None:
            conn.execute(items_table.update().where(items_table.c.id == item.id).values(
                dependency_key=chosen.from_key, dependency_type=chosen.type, dependency_lag=chosen.lag))
        elif item.dependency_key is not None and (item.plan_id, item.key) in first:
            # Связи строки сняты в 0.7.0 — предшественника нет; строки без связей остаются как были до обновления
            conn.execute(items_table.update().where(items_table.c.id == item.id).values(dependency_key=None))

    with op.batch_alter_table('plan_items', schema=None) as batch_op:
        batch_op.drop_column('constraint_date')
        batch_op.drop_column('constraint_type')
        batch_op.drop_column('deadline')
    with op.batch_alter_table('plan_links', schema=None) as batch_op:
        batch_op.drop_index('ix_plan_links_plan_rev')
        batch_op.drop_index(batch_op.f('ix_plan_links_plan_id'))
    op.drop_table('plan_links')
