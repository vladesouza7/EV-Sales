"""S-03 §2 — testes da tool `buscar_conhecimento` e do handoff na etapa.

Verifica que Aurora pode consultar a base de conhecimento sobre rotas (Recife/BR-101),
baterias, garantia, recarga residencial/pública e manutenção para quebrar objeções
com dados confiáveis da Sol & Volt.
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.pii import cifrar, hash_telefone
from app.db import agora
from app.ia.registro import disponiveis, esquemas, executar
from app.ia.tools.conhecimento import buscar_conhecimento, semear_conhecimento
from app.ia.turno import executar_turno
from app.modelos import Conversa, Lead, Mensagem, Trilha

from .dubles import ProvedorDuble


@pytest.fixture
def base_conhecimento(sessao: Session) -> Session:
    semear_conhecimento(sessao)
    return sessao


@pytest.fixture
def conversa_objecao(sessao: Session) -> Conversa:
    lead = Lead(
        nome_cifrado=cifrar("Rogério Silva"),
        telefone_cifrado=cifrar("+5583988711122"),
        telefone_hash=hash_telefone("+5583988711122"),
    )
    sessao.add(lead)
    sessao.flush()
    conversa = Conversa(
        lead_id=lead.id,
        etapa="objecao",
        token_sessao=uuid.uuid4().hex,
        token_expira_em=agora(),
    )
    sessao.add(conversa)
    sessao.commit()
    return conversa


def test_buscar_rota_recife_br101(base_conhecimento: Session) -> None:
    itens = buscar_conhecimento(base_conhecimento, "estrada de Joao Pessoa a Recife BR-101")
    assert len(itens) >= 1
    topico = itens[0]
    assert topico["topico"] == "estrada_recife_br101"
    assert "120 km" in str(topico["conteudo"])
    assert "BR-101" in str(topico["titulo"])


def test_buscar_durabilidade_e_risco_de_viciar(base_conhecimento: Session) -> None:
    itens = buscar_conhecimento(base_conhecimento, "a bateria do carro eletrico vicia?")
    assert len(itens) >= 1
    topico = itens[0]
    assert topico["topico"] == "vida_util_bateria"
    assert "não viciam" in str(topico["conteudo"]) or "nao viciam" in str(topico["conteudo"])


def test_buscar_garantia_da_bateria(base_conhecimento: Session) -> None:
    itens = buscar_conhecimento(base_conhecimento, "qual a garantia de fabrica da bateria?")
    assert len(itens) >= 1
    encontrados = {i["topico"] for i in itens}
    assert "garantia_baterias" in encontrados or "vida_util_bateria" in encontrados
    garantia = next(i for i in itens if i["topico"] == "garantia_baterias")
    assert "8 anos" in str(garantia["conteudo"])


def test_buscar_carregamento_residencial(base_conhecimento: Session) -> None:
    itens = buscar_conhecimento(base_conhecimento, "como carregar em casa na tomada 220V?")
    assert len(itens) >= 1
    topicos = {i["topico"] for i in itens}
    assert "carregamento_residencial" in topicos or "tempo_de_recarga" in topicos


def test_buscar_termo_vazio_ou_sem_nexo_retorna_vazio(base_conhecimento: Session) -> None:
    assert buscar_conhecimento(base_conhecimento, "") == []
    assert buscar_conhecimento(base_conhecimento, "    ") == []
    assert buscar_conhecimento(base_conhecimento, "xyzqwk1234567") == []


def test_buscar_conhecimento_disponivel_nas_etapas_corretas() -> None:
    for etapa in ("qualificacao", "recomendacao", "objecao"):
        assert "buscar_conhecimento" in disponiveis(etapa), f"deve estar em {etapa}"
    for etapa in ("saudacao", "condicao", "reserva", "aguardando_aprovacao", "humano", "encerrada"):
        assert "buscar_conhecimento" not in disponiveis(etapa), f"não deve estar em {etapa}"


def test_transferir_para_humano_disponivel_em_toda_etapa_com_aurora() -> None:
    for etapa in (
        "saudacao",
        "qualificacao",
        "recomendacao",
        "objecao",
        "condicao",
        "reserva",
        "test_drive",
    ):
        assert "transferir_para_humano" in disponiveis(etapa), (
            f"transferir_para_humano deve estar em {etapa}"
        )
    assert "transferir_para_humano" not in disponiveis("aguardando_aprovacao")
    assert "transferir_para_humano" not in disponiveis("humano")


def test_esquema_da_tool_buscar_conhecimento() -> None:
    from typing import Any, cast

    esq = esquemas("objecao")
    tool_conhecimento = next(
        t for t in esq if cast(dict[str, Any], t["function"])["name"] == "buscar_conhecimento"
    )
    fn = cast(dict[str, Any], tool_conhecimento["function"])
    params = cast(dict[str, Any], fn["parameters"])
    props = cast(dict[str, Any], params["properties"])
    assert "termo" in props
    assert "termo" in cast(list[str], params["required"])


def test_executar_buscar_conhecimento_via_registro(
    base_conhecimento: Session, conversa_objecao: Conversa
) -> None:
    resultado = executar(
        base_conhecimento,
        conversa_objecao,
        "buscar_conhecimento",
        {"termo": "viagem Recife BR-101"},
    )
    assert isinstance(resultado, list)
    assert len(resultado) > 0
    assert resultado[0]["topico"] == "estrada_recife_br101"


def test_turno_da_aurora_com_buscar_conhecimento(
    base_conhecimento: Session, conversa_objecao: Conversa, provedor: ProvedorDuble
) -> None:
    provedor.chamar_tool("buscar_conhecimento", termo="estrada Recife")
    provedor.responder(
        "A viagem para Recife pela BR-101 é tranquila, são cerca de 120 km e nossos carros "
        "fazem com folga sem precisar recarregar no caminho."
    )

    entrada = Mensagem(
        conversa_id=conversa_objecao.id,
        direcao="entrada",
        autor="cliente",
        canal="web",
        conteudo="elétrico aguenta ir pra Recife pela BR-101?",
    )
    base_conhecimento.add(entrada)
    base_conhecimento.commit()

    from typing import Any

    async def _rodar() -> list[tuple[str, dict[str, Any]]]:
        eventos = []
        async for ev, dados in executar_turno(base_conhecimento, conversa_objecao, entrada):
            eventos.append((ev, dados))
        return eventos

    import asyncio

    eventos = asyncio.run(_rodar())

    nomes_tools = [d["nome"] for ev, d in eventos if ev == "tool_inicio"]
    assert "buscar_conhecimento" in nomes_tools

    trilhas = list(
        base_conhecimento.scalars(
            select(Trilha).where(
                Trilha.conversa_id == conversa_objecao.id,
                Trilha.tipo == "tool",
                Trilha.nome == "buscar_conhecimento",
            )
        )
    )
    assert len(trilhas) == 1
    assert trilhas[0].dados["argumentos"] == {"termo": "estrada Recife"}
