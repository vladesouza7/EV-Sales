"""S-07 §6: carimbo do lembrete único

O lembrete é **único** (§6), e a rotina roda de 5 em 5 minutos: sem um carimbo, o cliente
receberia a mesma mensagem 288 vezes por dia. Uma coluna, e não uma consulta na trilha
procurando o evento: `lembrete_em` também é o que a tela do vendedor lê para saber que o
cliente foi avisado e não respondeu.

O carimbo é gravado **nos dois casos** — mensagem enviada e tarefa de ligação criada —,
porque o que ele registra é "o lembrete desta visita já foi tratado".

Revision ID: b8e41c07d2a9
Revises: a7f2c91d4b60
Create Date: 2026-09-07 11:48:19.402318
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8e41c07d2a9"
down_revision: str | None = "a7f2c91d4b60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "test_drives", sa.Column("lembrete_em", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("test_drives", "lembrete_em")
