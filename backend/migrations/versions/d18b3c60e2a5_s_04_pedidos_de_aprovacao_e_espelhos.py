"""S-04: pedidos_de_aprovacao e espelhos

⚠️  MIGRATION DE REVISÃO HUMANA OBRIGATÓRIA (CLAUDE.md).

Estas duas tabelas são o que garante a invariante 3: `espelhos.approval_id` é NOT NULL com
foreign key, e não existe caminho de código que emita um espelho sem aprovação. Se alguém
tornar essa coluna nula — inclusive "temporariamente, para testar" — a invariante morre em
silêncio e a Neuza deixa de ser o portão do irreversível.

O índice parcial `ux_pedidos_um_pendente_por_conversa` é a segunda garantia: a etapa
`aguardando_aprovacao` não tem tools, então a Aurora não consegue pedir duas vezes, mas
lista de tools é configuração e índice é banco.

A sequência `espelhos_numero_seq` numera o documento sem `SELECT count(*)`, que é onde
duas aprovações simultâneas receberiam o mesmo número.

Revision ID: d18b3c60e2a5
Revises: c72a5e18d940
Create Date: 2026-09-06

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d18b3c60e2a5"
down_revision: str | None = "c72a5e18d940"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pedidos_de_aprovacao",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversa_id", sa.Uuid(), nullable=False),
        sa.Column("chassi", sa.String(length=17), nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=False),
        sa.Column("preco_centavos", sa.BigInteger(), nullable=False),
        sa.Column("qualificacao_resumo", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("decidido_por", sa.Uuid(), nullable=True),
        sa.Column("decidido_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("motivo_rejeicao", sa.String(length=40), nullable=True),
        sa.Column("ip_da_decisao", sa.String(length=45), nullable=True),
        sa.Column("codigo", sa.String(length=8), nullable=False),
        sa.Column("codigo_usado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("escalado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expira_em", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pendente', 'aprovado', 'rejeitado', 'expirado')", name="ck_pedidos_status"
        ),
        sa.CheckConstraint("preco_centavos > 0", name="ck_pedidos_preco_positivo"),
        sa.CheckConstraint(
            "(decidido_por IS NULL) = (decidido_em IS NULL)", name="ck_pedidos_decisao_completa"
        ),
        sa.ForeignKeyConstraint(["conversa_id"], ["conversas.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chassi"], ["unidades.chassi"]),
        sa.ForeignKeyConstraint(["decidido_por"], ["usuarios.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_pedidos_de_aprovacao_codigo"), "pedidos_de_aprovacao", ["codigo"], unique=True)
    op.create_index("ix_pedidos_status_criado", "pedidos_de_aprovacao", ["status", "criado_em"])
    op.create_index(
        "ux_pedidos_um_pendente_por_conversa",
        "pedidos_de_aprovacao",
        ["conversa_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pendente'"),
    )

    op.execute("CREATE SEQUENCE espelhos_numero_seq")
    op.create_table(
        "espelhos",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversa_id", sa.Uuid(), nullable=False),
        sa.Column("chassi", sa.String(length=17), nullable=False),
        # ← A INVARIANTE 3. Nunca torne esta coluna nula.
        sa.Column("approval_id", sa.Uuid(), nullable=False),
        sa.Column("preco_centavos", sa.BigInteger(), nullable=False),
        sa.Column("numero", sa.String(length=16), nullable=False),
        sa.Column("pdf_objeto", sa.String(length=120), nullable=False),
        sa.Column("valido_ate", sa.DateTime(timezone=True), nullable=False),
        sa.Column("emitido_em", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("preco_centavos > 0", name="ck_espelhos_preco_positivo"),
        sa.ForeignKeyConstraint(["conversa_id"], ["conversas.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chassi"], ["unidades.chassi"]),
        sa.ForeignKeyConstraint(["approval_id"], ["pedidos_de_aprovacao.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("numero"),
    )
    op.create_index("ux_espelhos_por_aprovacao", "espelhos", ["approval_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ux_espelhos_por_aprovacao", table_name="espelhos")
    op.drop_table("espelhos")
    op.execute("DROP SEQUENCE espelhos_numero_seq")
    op.drop_index("ux_pedidos_um_pendente_por_conversa", table_name="pedidos_de_aprovacao")
    op.drop_index("ix_pedidos_status_criado", table_name="pedidos_de_aprovacao")
    op.drop_index(op.f("ix_pedidos_de_aprovacao_codigo"), table_name="pedidos_de_aprovacao")
    op.drop_table("pedidos_de_aprovacao")
