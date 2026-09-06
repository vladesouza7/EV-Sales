"""Seed mínimo para a S-01: o catálogo público precisa de estoque para mostrar.

Os chassis **e os preços** aqui são fictícios e estão marcados como tal. Autonomia e
fonte vêm de docs/pesquisa/CATALOGO-E-OBJECOES.md — nenhum número foi estimado, e o
que não tem fonte confirmada entra `NULL` (ADR-003).

O seed completo da S-10 (58 modelos, 17 unidades, vendedores, base de conhecimento)
ainda não existe. Este cobre o que a S-01 precisa para funcionar de ponta a ponta.

Idempotente: rodar duas vezes não duplica.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy.dialects.postgresql import insert  # noqa: E402

from app.core.pii import cifrar  # noqa: E402
from app.db import Sessao  # noqa: E402
from app.modelos import Unidade, Vendedor  # noqa: E402

UNIDADES = [
    dict(
        chassi="9BWZZZ377VT100001",
        marca="BYD",
        modelo="Dolphin Mini",
        versao="GL",
        ano=2026,
        cor="Branco",
        condicao="novo",
        km=0,
        preco_centavos=11890000,
        autonomia_km=280,
        autonomia_fonte="INMETRO_PBEV_2026",
        foto_url="/static/fotos/dolphin-mini-branco.jpg",
    ),
    dict(
        chassi="9BWZZZ377VT100002",
        marca="BYD",
        modelo="Dolphin",
        versao="GS",
        ano=2026,
        cor="Azul",
        condicao="novo",
        km=0,
        preco_centavos=14999000,
        autonomia_km=291,
        autonomia_fonte="INMETRO_PBEV_2026",
        foto_url="/static/fotos/dolphin-azul.jpg",
    ),
    # O par que o Raí citou: mesmo modelo, duas cores, um de cada. Não existe "outro igual".
    dict(
        chassi="9BWZZZ377VT004471",
        marca="BYD",
        modelo="Seal",
        versao="Design",
        ano=2026,
        cor="Branco",
        condicao="novo",
        km=0,
        preco_centavos=24999000,
        autonomia_km=372,
        autonomia_fonte="INMETRO_PBEV_2026",
        foto_url="/static/fotos/seal-branco.jpg",
    ),
    dict(
        chassi="9BWZZZ377VT009902",
        marca="BYD",
        modelo="Seal",
        versao="Design",
        ano=2026,
        cor="Cinza",
        condicao="novo",
        km=0,
        preco_centavos=24999000,
        autonomia_km=372,
        autonomia_fonte="INMETRO_PBEV_2026",
        foto_url="/static/fotos/seal-cinza.jpg",
    ),
    dict(
        chassi="9BGZZZ377VT100005",
        marca="Chevrolet",
        modelo="Blazer EV",
        versao="RS",
        ano=2026,
        cor="Preto",
        condicao="novo",
        km=0,
        preco_centavos=50319000,
        autonomia_km=481,
        autonomia_fonte="INMETRO_PBEV_2026",
        foto_url="/static/fotos/blazer-ev-rs.jpg",
    ),
    # Importado: publica WLTP, e o rótulo viaja junto. WLTP e Inmetro não se comparam.
    dict(
        chassi="5YJYGDEE1LF100006",
        marca="Tesla",
        modelo="Model Y",
        versao="Long Range",
        ano=2024,
        cor="Branco",
        condicao="seminovo",
        km=28400,
        preco_centavos=44990000,
        autonomia_km=533,
        autonomia_fonte="WLTP",
        foto_url="/static/fotos/model-y-branco.jpg",
    ),
    # Sem número confirmado para esta versão: entra NULL, e a Aurora diz "vou confirmar".
    dict(
        chassi="WP0ZZZY1ZKSA09902",
        marca="Porsche",
        modelo="Taycan",
        versao="4S",
        ano=2023,
        cor="Cinza",
        condicao="seminovo",
        km=31000,
        preco_centavos=52900000,
        autonomia_km=None,
        autonomia_fonte=None,
        foto_url="/static/fotos/taycan-cinza.jpg",
    ),
]


# S-07 — a equipe da Sol & Volt. Telefones fictícios e cifrados como qualquer outro
# (ADR-007): vendedor é PII também.
VENDEDORES = [
    ("Jaqueline", "+5583988710002"),
    ("Tarcísio", "+5583988710001"),
]


def semear() -> None:
    with Sessao() as sessao:
        sessao.execute(
            insert(Unidade).values(UNIDADES).on_conflict_do_nothing(index_elements=["chassi"])
        )
        sessao.execute(
            insert(Vendedor)
            .values(
                [
                    dict(nome=nome, telefone_cifrado=cifrar(telefone), ativo=True)
                    for nome, telefone in VENDEDORES
                ]
            )
            .on_conflict_do_nothing(index_elements=["nome"])
        )
        sessao.commit()
        total = sessao.query(Unidade).count()
        equipe = sessao.query(Vendedor).count()
    print(f"seed ok · {total} unidades no estoque · {equipe} vendedores na agenda")


if __name__ == "__main__":
    semear()
