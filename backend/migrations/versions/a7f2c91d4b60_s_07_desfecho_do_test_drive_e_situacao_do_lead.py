"""S-07 §9: desfecho do test drive e situação do lead

`vendeu` é o único ponto em que o EV-Sales sabe que a venda aconteceu, e a S-07 §9 diz que
o sistema **nunca** marca isso sozinho — só por toque de vendedor autenticado, com
auditoria. Por isso o CHECK `ck_test_drives_desfecho_tem_autor`: desfecho sem quem marcou e
sem quando é impossível no banco, não apenas improvável no código.

`leads.situacao` nasce com `server_default` porque a tabela já tem linhas: sem ele, o
`NOT NULL` recusa a migration num banco com lead cadastrado.

O índice parcial é a consulta da cobrança (§9, "contra o esquecimento"): a rotina roda de 5
em 5 minutos e pergunta quais test drives já passaram e continuam sem desfecho.

Revision ID: a7f2c91d4b60
Revises: 543c276dc469
Create Date: 2026-09-07 10:12:03.114522
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7f2c91d4b60"
down_revision: str | None = "543c276dc469"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESFECHOS = ("vendeu", "vai_pensar", "desistiu", "nao_compareceu")
SITUACOES = ("novo", "em_negociacao", "ganho", "perdido", "a_recontatar")


def _em(coluna: str, valores: Sequence[str]) -> str:
    return f"{coluna} IN (" + ", ".join(f"'{v}'" for v in valores) + ")"


def upgrade() -> None:
    op.add_column("test_drives", sa.Column("compareceu", sa.Boolean(), nullable=True))
    op.add_column("test_drives", sa.Column("desfecho", sa.String(length=16), nullable=True))
    op.add_column(
        "test_drives", sa.Column("desfecho_em", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("test_drives", sa.Column("desfecho_por", sa.Uuid(), nullable=True))
    op.add_column(
        "test_drives", sa.Column("cobrancas", sa.Integer(), nullable=False, server_default="0")
    )
    op.create_foreign_key(
        "fk_test_drives_desfecho_por",
        "test_drives",
        "usuarios",
        ["desfecho_por"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_test_drives_desfecho",
        "test_drives",
        f"desfecho IS NULL OR {_em('desfecho', DESFECHOS)}",
    )
    op.create_check_constraint(
        "ck_test_drives_desfecho_tem_autor",
        "test_drives",
        "(desfecho IS NULL) = (desfecho_por IS NULL)"
        " AND (desfecho IS NULL) = (desfecho_em IS NULL)",
    )
    op.create_index(
        "ix_test_drives_sem_desfecho",
        "test_drives",
        ["inicio"],
        postgresql_where=sa.text("desfecho IS NULL"),
    )

    op.add_column(
        "leads",
        sa.Column("situacao", sa.String(length=16), nullable=False, server_default="novo"),
    )
    op.create_check_constraint("ck_leads_situacao", "leads", _em("situacao", SITUACOES))


def downgrade() -> None:
    op.drop_constraint("ck_leads_situacao", "leads", type_="check")
    op.drop_column("leads", "situacao")
    op.drop_index("ix_test_drives_sem_desfecho", table_name="test_drives")
    op.drop_constraint("ck_test_drives_desfecho_tem_autor", "test_drives", type_="check")
    op.drop_constraint("ck_test_drives_desfecho", "test_drives", type_="check")
    op.drop_constraint("fk_test_drives_desfecho_por", "test_drives", type_="foreignkey")
    op.drop_column("test_drives", "cobrancas")
    op.drop_column("test_drives", "desfecho_por")
    op.drop_column("test_drives", "desfecho_em")
    op.drop_column("test_drives", "desfecho")
    op.drop_column("test_drives", "compareceu")
