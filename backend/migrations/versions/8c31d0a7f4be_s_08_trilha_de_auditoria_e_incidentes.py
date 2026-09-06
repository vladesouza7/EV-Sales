"""S-08: trilha de auditoria e incidentes

A origem única do ADR-006. Duas tabelas novas e nenhuma alteração nas existentes: a
instrumentação entra antes do agente, e entrar antes não pode significar mexer no que
já protege as invariantes.

`conversa_id` é `ON DELETE SET NULL` nas duas: a retenção da S-09 §6 apaga o lead, e o
gasto do mês não pode sumir junto com ele.

Revision ID: 8c31d0a7f4be
Revises: 41b578425ada
Create Date: 2026-09-05

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "8c31d0a7f4be"
down_revision: str | None = "41b578425ada"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trilha",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversa_id", sa.Uuid(), nullable=True),
        sa.Column("tipo", sa.String(length=12), nullable=False),
        sa.Column("nome", sa.String(length=40), nullable=False),
        sa.Column("dados", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("duracao_ms", sa.Integer(), nullable=True),
        sa.Column("custo_micro_reais", sa.BigInteger(), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "tipo IN ('turno', 'tool', 'verificacao', 'evento')", name="ck_trilha_tipo"
        ),
        sa.CheckConstraint("custo_micro_reais >= 0", name="ck_trilha_custo_nao_negativo"),
        sa.ForeignKeyConstraint(["conversa_id"], ["conversas.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_trilha_conversa_criado", "trilha", ["conversa_id", "criado_em"])
    op.create_index("ix_trilha_criado", "trilha", ["criado_em"])

    op.create_table(
        "incidentes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversa_id", sa.Uuid(), nullable=True),
        sa.Column("tipo", sa.String(length=30), nullable=False),
        sa.Column("gravidade", sa.String(length=12), nullable=False),
        sa.Column("dados", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "gravidade IN ('critica', 'alta', 'baixa', 'informativa')",
            name="ck_incidentes_gravidade",
        ),
        sa.ForeignKeyConstraint(["conversa_id"], ["conversas.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_incidentes_tipo_criado", "incidentes", ["tipo", "criado_em"])


def downgrade() -> None:
    op.drop_index("ix_incidentes_tipo_criado", table_name="incidentes")
    op.drop_table("incidentes")
    op.drop_index("ix_trilha_criado", table_name="trilha")
    op.drop_index("ix_trilha_conversa_criado", table_name="trilha")
    op.drop_table("trilha")
