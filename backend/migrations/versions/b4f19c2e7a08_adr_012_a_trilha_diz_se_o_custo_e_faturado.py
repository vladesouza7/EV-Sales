"""ADR-012: a trilha diz se o custo é faturado

Com o provedor virando configuração, só o OpenRouter devolve custo faturado. Gravar `0`
para todos faria o painel do Raí mostrar R$ 0,00 para uma API paga, e o teto de R$ 900
deixaria de proteger em silêncio.

`server_default='true'` para a linha que já existe: tudo o que foi gravado até aqui veio
do OpenRouter, e veio faturado.

Revision ID: b4f19c2e7a08
Revises: 8c31d0a7f4be
Create Date: 2026-09-06

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4f19c2e7a08"
down_revision: str | None = "8c31d0a7f4be"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "trilha",
        sa.Column("custo_faturado", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("trilha", "custo_faturado")
