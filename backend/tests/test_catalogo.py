"""S-01 §6 — a saída pelo catálogo somente-leitura."""

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ia.tools.estoque import buscar_unidades
from app.modelos import Lead, Unidade

SEAL_BRANCO = dict(
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
    foto_url="/fotos/seal-branco.jpg",
    status="disponivel",
)
TAYCAN = dict(
    SEAL_BRANCO,
    chassi="WP0ZZZY1ZKSA09902",
    marca="Porsche",
    modelo="Taycan",
    versao="4S",
    cor="Cinza",
    condicao="seminovo",
    km=31000,
    preco_centavos=52900000,
    autonomia_km=None,
    autonomia_fonte=None,
)


def _semear(sessao: Session, *unidades: dict[str, object]) -> None:
    sessao.add_all(Unidade(**u) for u in unidades)  # type: ignore[arg-type]
    sessao.commit()


def test_lista_as_unidades_disponiveis(cliente: TestClient, sessao: Session) -> None:
    _semear(sessao, SEAL_BRANCO)

    corpo = cliente.get("/api/catalogo").json()

    assert len(corpo) == 1
    carro = corpo[0]
    assert carro["modelo"] == "Seal"
    assert carro["versao"] == "Design"
    assert carro["ano"] == 2026
    assert carro["cor"] == "Branco"
    assert carro["preco_centavos"] == 24999000
    assert carro["autonomia_km"] == 372
    assert carro["autonomia_fonte"] == "INMETRO_PBEV_2026"
    assert carro["foto_url"]


def test_unidade_reservada_some_do_catalogo(cliente: TestClient, sessao: Session) -> None:
    _semear(sessao, SEAL_BRANCO)
    unidade = sessao.scalars(select(Unidade)).one()
    unidade.status = "reservado"
    sessao.commit()

    assert cliente.get("/api/catalogo").json() == []


def test_autonomia_desconhecida_vem_nula_nunca_estimada(
    cliente: TestClient, sessao: Session
) -> None:
    _semear(sessao, TAYCAN)

    carro = cliente.get("/api/catalogo").json()[0]
    assert carro["autonomia_km"] is None
    assert carro["autonomia_fonte"] is None


def test_catalogo_e_aurora_leem_a_mesma_tool(cliente: TestClient, sessao: Session) -> None:
    _semear(sessao, SEAL_BRANCO, TAYCAN)

    with Session(sessao.get_bind()) as outra:
        da_tool = buscar_unidades(outra)

    assert cliente.get("/api/catalogo").json() == da_tool


def test_pagina_do_catalogo_nao_tem_chat_nem_cadastro(cliente: TestClient) -> None:
    """S-01 §6: somente leitura, sem chat, sem cadastro."""
    pagina = cliente.get("/catalogo")

    assert pagina.status_code == 200
    # Nada que receba texto do cliente: nem formulário, nem campo, nem POST.
    assert "<form" not in pagina.text
    assert "<input" not in pagina.text
    assert "/api/leads" not in pagina.text
    assert "interesse=" in pagina.text


def test_olhar_o_catalogo_nao_cria_lead(cliente: TestClient, sessao: Session) -> None:
    cliente.get("/catalogo")
    cliente.get("/api/catalogo")

    assert sessao.scalars(select(Lead)).all() == []


def test_landing_tem_a_saida_pelo_lado(cliente: TestClient) -> None:
    """ADR-010 §2 — a saída existe e leva ao catálogo sem passar por cadastro.

    O link discreto *"prefiro só olhar os carros"* virou o botão "Conheça os modelos"
    no herói (S-01 §1.3). O que o ADR decidiu é que a saída existe, e é isso que este
    teste guarda — não a redação de um link.
    """
    pagina = cliente.get("/")

    assert pagina.status_code == 200
    assert 'href="/catalogo"' in pagina.text
    assert "Conheça os modelos" in pagina.text
    assert "Sol &amp; Volt" in pagina.text or "Sol & Volt" in pagina.text
    assert "Tambaú" in pagina.text


def test_catalogo_continua_sem_cadastro_e_sem_chat(cliente: TestClient) -> None:
    """A saída só é saída enquanto o catálogo não pedir nada em troca."""
    pagina = cliente.get("/catalogo")

    assert pagina.status_code == 200
    assert "/api/leads" not in pagina.text
    assert 'id="telefone"' not in pagina.text


def test_ofertas_e_o_mesmo_estoque_sem_preco_promocional(
    cliente: TestClient, sessao: Session
) -> None:
    """Invariante 2 — não existe desconto no sistema, e a vitrine não pode inventar um.

    A garantia não é a redação do HTML: é que /ofertas lê a mesma tool do catálogo e que
    a resposta não tem campo nenhum onde um "de/por" poderia morar.
    """
    _semear(sessao, SEAL_BRANCO, TAYCAN)
    pagina = cliente.get("/ofertas")

    assert pagina.status_code == 200
    assert "/api/catalogo" in pagina.text, "lê a mesma tool do catálogo"
    assert "de R$" not in pagina.text

    for carro in cliente.get("/api/catalogo").json():
        assert not {"preco_antigo", "preco_promocional", "desconto"} & set(carro)


def test_ofertas_lista_do_menor_para_o_maior_preco(
    cliente: TestClient, sessao: Session
) -> None:
    _semear(sessao, TAYCAN, SEAL_BRANCO)

    precos = [carro["preco_centavos"] for carro in cliente.get("/api/catalogo").json()]

    assert precos == sorted(precos)
