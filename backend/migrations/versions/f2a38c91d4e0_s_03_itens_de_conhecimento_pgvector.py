"""S-03 §2, S-10 §4 e ADR-002: base de conhecimento com pgvector

Extensão vector no mesmo Postgres (ADR-002) e tabela itens_de_conhecimento para busca
semântica das quatro objeções clássicas, carregamento, garantia e rota João Pessoa-Recife.

Revision ID: f2a38c91d4e0
Revises: b8e41c07d2a9
Create Date: 2026-09-07 17:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "f2a38c91d4e0"
down_revision: str | None = "b8e41c07d2a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "itens_de_conhecimento",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("topico", sa.String(length=60), nullable=False),
        sa.Column("categoria", sa.String(length=40), nullable=False),
        sa.Column("titulo", sa.String(length=140), nullable=False),
        sa.Column("conteudo", sa.Text(), nullable=False),
        sa.Column("palavras_chave", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "categoria IN ('objecao', 'garantia', 'carregamento', 'rota', 'manutencao')",
            name="ck_itens_conhecimento_categoria",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("topico", name="ux_itens_conhecimento_topico"),
    )
    op.create_index(
        "ix_itens_conhecimento_categoria",
        "itens_de_conhecimento",
        ["categoria"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_itens_conhecimento_categoria", table_name="itens_de_conhecimento")
    op.drop_table("itens_de_conhecimento")
