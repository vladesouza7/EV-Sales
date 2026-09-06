"""S-05: reservas

⚠️  MIGRATION DE REVISÃO HUMANA OBRIGATÓRIA (CLAUDE.md): cria `reservas` e é a tabela que
sustenta a invariante 4.

Quem decide a corrida entre dois clientes é o `UPDATE unidades … WHERE status='disponivel'`,
não esta tabela. Os dois índices parciais são a segunda linha de defesa: mesmo que alguém
troque aquele UPDATE por um SELECT seguido de INSERT — o erro que o CLAUDE.md registra como
já tendo acontecido aqui —, o banco recusa a segunda reserva ativa do mesmo chassi.

Remover qualquer um dos dois índices reabre a janela em que o Raí precisa telefonar para um
cliente e desmarcar.

Revision ID: e5c07a91b6f3
Revises: d18b3c60e2a5
Create Date: 2026-09-06

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5c07a91b6f3"
down_revision: str | None = "d18b3c60e2a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reservas",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("chassi", sa.String(length=17), nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=False),
        sa.Column("conversa_id", sa.Uuid(), nullable=False),
        # Invariante 3: reserva exige aprovação válida. Nunca torne estas duas nulas.
        sa.Column("approval_id", sa.Uuid(), nullable=False),
        sa.Column("espelho_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("renovacoes", sa.Integer(), nullable=False),
        sa.Column("criada_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expira_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("liberada_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("motivo_liberacao", sa.String(length=30), nullable=True),
        sa.CheckConstraint("status IN ('ativa', 'liberada', 'concluida')", name="ck_reservas_status"),
        sa.CheckConstraint("renovacoes BETWEEN 0 AND 2", name="ck_reservas_renovacoes"),
        sa.CheckConstraint("expira_em > criada_em", name="ck_reservas_prazo_positivo"),
        sa.ForeignKeyConstraint(["chassi"], ["unidades.chassi"]),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversa_id"], ["conversas.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["approval_id"], ["pedidos_de_aprovacao.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["espelho_id"], ["espelhos.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    # ← As duas linhas abaixo são a invariante 4 em forma de índice.
    op.create_index(
        "ux_reservas_uma_ativa_por_chassi",
        "reservas",
        ["chassi"],
        unique=True,
        postgresql_where=sa.text("status = 'ativa'"),
    )
    op.create_index(
        "ux_reservas_uma_ativa_por_lead",
        "reservas",
        ["lead_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ativa'"),
    )
    op.create_index("ix_reservas_status_expira", "reservas", ["status", "expira_em"])


def downgrade() -> None:
    op.drop_index("ix_reservas_status_expira", table_name="reservas")
    op.drop_index("ux_reservas_uma_ativa_por_lead", table_name="reservas")
    op.drop_index("ux_reservas_uma_ativa_por_chassi", table_name="reservas")
    op.drop_table("reservas")
