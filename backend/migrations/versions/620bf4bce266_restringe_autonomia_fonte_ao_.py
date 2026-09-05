"""restringe autonomia_fonte ao vocabulario da S-03

REVISÃO HUMANA OBRIGATÓRIA (CLAUDE.md): mexe em `unidades`.

`autonomia_fonte` era `String(30)` livre, e o repositório já tinha gravado duas
grafias para a mesma coisa (`INMETRO_PBEV` e `INMETRO_PBEV_2026`). Enquanto o rótulo
depender de convenção, a invariante 6 depende de alguém lembrar — e o CLAUDE.md manda
resolver no código. O vocabulário é o declarado na S-03 §1.

Revision ID: 620bf4bce266
Revises: 24ea6deee20e
Create Date: 2026-09-04 23:43:03.136757
"""

from collections.abc import Sequence

from alembic import op

revision: str = "620bf4bce266"
down_revision: str | None = "24ea6deee20e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RESTRICAO = "ck_unidades_fonte_de_autonomia_conhecida"
VOCABULARIO = "('INMETRO_PBEV_2026', 'WLTP', 'FABRICANTE')"


def upgrade() -> None:
    # Normaliza a grafia antiga antes de restringir, senão a constraint não aplica
    # sobre banco que já foi semeado.
    op.execute(
        "UPDATE unidades SET autonomia_fonte = 'INMETRO_PBEV_2026' "
        "WHERE autonomia_fonte = 'INMETRO_PBEV'"
    )
    op.create_check_constraint(
        RESTRICAO,
        "unidades",
        f"autonomia_fonte IS NULL OR autonomia_fonte IN {VOCABULARIO}",
    )


def downgrade() -> None:
    # Só a restrição volta atrás. A normalização da grafia é de mão única de
    # propósito: reintroduzir `INMETRO_PBEV` seria recriar a ambiguidade.
    op.drop_constraint(RESTRICAO, "unidades", type_="check")
