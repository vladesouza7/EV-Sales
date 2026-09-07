"""S-12: configuracoes e telefone de aviso

A lista de chaves vira CHECK, e não só `Enum` em Python: instrução em prompt some no diff,
constraint de banco não. Chave nova passa a exigir commit **e** migration, que é a porta
mais pesada que existe aqui — e é de propósito, porque esta tabela é a candidata natural a
virar porta lateral para o domínio (ADR-014 §1).

`usuarios.telefone_cifrado` é nulo: quem não quer receber aviso não tem telefone cadastrado,
e não existe telefone de funcionário em claro em lugar nenhum (ADR-007).

Revision ID: e0befdee10dc
Revises: e5c07a91b6f3
Create Date: 2026-09-06 21:04:48.699975
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e0befdee10dc"
down_revision: str | None = "e5c07a91b6f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHAVES = (
    "whatsapp_numero",
    "evolution_url",
    "evolution_instancia",
    "evolution_chave",
    "llm_provedor",
    "llm_modelo",
    "llm_url",
    "llm_chave",
    "llm_fallbacks",
)


def upgrade() -> None:
    op.create_table(
        "configuracoes",
        sa.Column("chave", sa.String(length=40), nullable=False),
        sa.Column("valor_cifrado", sa.LargeBinary(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("atualizado_por", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["atualizado_por"], ["usuarios.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("chave"),
        sa.CheckConstraint(
            "chave IN (" + ", ".join(f"'{c}'" for c in CHAVES) + ")",
            name="ck_configuracoes_chave_conhecida",
        ),
    )
    op.add_column("usuarios", sa.Column("telefone_cifrado", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    op.drop_column("usuarios", "telefone_cifrado")
    op.drop_table("configuracoes")
