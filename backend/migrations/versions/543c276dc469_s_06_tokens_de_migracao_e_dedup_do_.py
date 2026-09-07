"""S-06: tokens de migracao e dedup do webhook

O índice único parcial em `whatsapp_message_id` é a deduplicação da S-06 §4.1: a Evolution
entrega *at-least-once*, e quem recusa a segunda gravação é o banco, não um `SELECT` antes
do `INSERT`. Parcial porque toda mensagem do chat web tem a coluna nula.

Revision ID: 543c276dc469
Revises: e0befdee10dc
Create Date: 2026-09-06 21:43:19.953253
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '543c276dc469'
down_revision: str | None = 'e0befdee10dc'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('tokens_migracao',
    sa.Column('token', sa.String(length=6), nullable=False),
    sa.Column('conversa_id', sa.Uuid(), nullable=False),
    sa.Column('telefone_hash_esperado', sa.String(length=64), nullable=True),
    sa.Column('criado_em', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expira_em', sa.DateTime(timezone=True), nullable=False),
    sa.Column('usado_em', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['conversa_id'], ['conversas.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('token')
    )
    op.create_index('ux_mensagens_whatsapp_id', 'mensagens', ['whatsapp_message_id'], unique=True, postgresql_where=sa.text('whatsapp_message_id IS NOT NULL'))


def downgrade() -> None:
    op.drop_index('ux_mensagens_whatsapp_id', table_name='mensagens', postgresql_where=sa.text('whatsapp_message_id IS NOT NULL'))
    op.drop_table('tokens_migracao')
