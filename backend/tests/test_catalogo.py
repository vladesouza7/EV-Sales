"""S-01 §6 — a saída pelo catálogo somente-leitura."""

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ia.tools.estoque import buscar_unidades
from app.modelos import Unidade

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


def test_pagina_do_catalogo_nao_tem_chat(cliente: TestClient) -> None:
    pagina = cliente.get("/catalogo")

    assert pagina.status_code == 200
    assert "prefiro só olhar" not in pagina.text
    assert "chat-input" not in pagina.text
    assert "interesse=" in pagina.text


def test_landing_tem_a_saida_pelo_lado(cliente: TestClient) -> None:
    pagina = cliente.get("/")

    assert pagina.status_code == 200
    assert "prefiro só olhar os carros" in pagina.text
    assert "Sol &amp; Volt" in pagina.text or "Sol & Volt" in pagina.text
    assert "Tambaú" in pagina.text
