"""S-11: usuarios

Uma tabela só. Não há `sessoes`: a sessão é um JWT de 20 minutos e a revogação é a coluna
`sessoes_validas_apos`, comparada com o `iat` do token.

Revision ID: c72a5e18d940
Revises: b4f19c2e7a08
Create Date: 2026-09-06

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c72a5e18d940"
down_revision: str | None = "b4f19c2e7a08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usuarios",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("nome", sa.String(length=60), nullable=False),
        sa.Column("email", sa.String(length=120), nullable=False),
        sa.Column("senha_hash", sa.String(length=200), nullable=False),
        sa.Column("perfil", sa.String(length=10), nullable=False),
        sa.Column("vendedor_id", sa.Uuid(), nullable=True),
        sa.Column("ativo", sa.Boolean(), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("senha_trocada_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tentativas_falhas", sa.Integer(), nullable=False),
        sa.Column("bloqueado_ate", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sessoes_validas_apos", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("perfil IN ('dono', 'gerente', 'vendedor')", name="ck_usuarios_perfil"),
        sa.CheckConstraint(
            "(perfil = 'vendedor') = (vendedor_id IS NOT NULL)",
            name="ck_usuarios_vendedor_tem_vinculo",
        ),
        sa.ForeignKeyConstraint(["vendedor_id"], ["vendedores.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_usuarios_email"), "usuarios", ["email"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_usuarios_email"), table_name="usuarios")
    op.drop_table("usuarios")
