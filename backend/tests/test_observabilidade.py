"""S-08 — critérios de aceite do trace, do custo e do teto.

A tela "Ler atendimento" (§4) e o painel de custo (§5) não estão aqui: os dois exigem
sessão autenticada, que ainda não existe no projeto. O que está aqui é a origem que os
dois leem — e é ela que a S-03 precisa ter pronta antes de o agente existir (ADR-006).
"""

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import agora
from app.modelos import Conversa, Incidente, Mensagem, Trilha, Unidade
from app.observabilidade import (
    ALERTA_MICRO_REAIS,
    CUSTO_ALTO_MICRO_REAIS,
    TETO_MICRO_REAIS,
    gasto_do_mes,
    pode_chamar_llm,
    registrar,
)

from .dubles import ProvedorDuble
from .test_conversas import LEAD, SEAL, _turno

MICRO = 10_000  # um centavo


@pytest.fixture
def conversa_id(cliente: TestClient) -> uuid.UUID:
    return uuid.UUID(cliente.post("/api/leads", json=LEAD).json()["conversa_id"])


def _mandar(cliente: TestClient, conversa_id: uuid.UUID, texto: str) -> None:
    resposta = cliente.post(f"/api/conversas/{conversa_id}/mensagens", json={"conteudo": texto})
    assert resposta.status_code == 201


def _gastar(sessao: Session, micro_reais: int, *, quando: object = None) -> None:
    """Gasto do mês sem passar pelo modelo: é a soma dos turnos que o teto lê."""
    linha = registrar(sessao, None, "turno", "aurora", custo_micro_reais=micro_reais)
    if quando is not None:
        linha.criado_em = quando  # type: ignore[assignment]
        sessao.commit()


def test_cada_tool_do_turno_vira_span_com_argumentos_e_retorno(
    cliente: TestClient, sessao: Session, conversa_id: uuid.UUID, provedor: ProvedorDuble
) -> None:
    provedor.chamar_tool("buscar_unidades", preco_max_centavos=30000000)
    provedor.responder("Tenho um Seal branco aqui, quer ver?")
    sessao.add(Unidade(**SEAL))
    conversa = sessao.get(Conversa, conversa_id)
    assert conversa is not None
    conversa.etapa = "recomendacao"
    sessao.commit()

    _mandar(cliente, conversa_id, "que carros vocês têm?")
    _turno(sessao, conversa_id)

    tools = sessao.scalars(
        select(Trilha).where(Trilha.conversa_id == conversa_id, Trilha.tipo == "tool")
    ).all()
    assert [t.nome for t in tools] == ["buscar_unidades"]
    assert "argumentos" in tools[0].dados and "retorno" in tools[0].dados
    assert tools[0].duracao_ms is not None


def test_o_turno_tem_span_proprio_com_latencia(
    cliente: TestClient, sessao: Session, conversa_id: uuid.UUID
) -> None:
    _mandar(cliente, conversa_id, "oi")
    _turno(sessao, conversa_id)

    turno = sessao.scalars(
        select(Trilha).where(Trilha.conversa_id == conversa_id, Trilha.tipo == "turno")
    ).one()
    assert turno.duracao_ms is not None
    assert turno.dados["etapa"] == "saudacao"


def test_o_gasto_do_mes_e_a_soma_dos_turnos(sessao: Session) -> None:
    _gastar(sessao, 3 * MICRO)
    _gastar(sessao, 7 * MICRO)
    # Turno do mês passado não conta para o teto deste mês.
    _gastar(sessao, 500 * MICRO, quando=agora() - timedelta(days=45))
    assert gasto_do_mes(sessao) == 10 * MICRO


def test_o_teto_corta_de_verdade(
    cliente: TestClient, sessao: Session, conversa_id: uuid.UUID
) -> None:
    _gastar(sessao, TETO_MICRO_REAIS)
    _mandar(cliente, conversa_id, "quero um elétrico até 150 mil")
    eventos = _turno(sessao, conversa_id)

    assert not pode_chamar_llm(sessao)
    texto = "".join(str(d["texto"]) for nome, d in eventos if nome == "token")
    assert "indisponível" in texto
    sessao.expire_all()
    conversa = sessao.get(Conversa, conversa_id)
    assert conversa is not None and conversa.modo == "humano"
    # Nenhum span de tool: o corte acontece antes de qualquer chamada.
    assert sessao.scalars(select(Trilha).where(Trilha.tipo == "tool")).all() == []


def test_o_alerta_de_80_por_cento_nao_se_repete_no_mesmo_dia(sessao: Session) -> None:
    _gastar(sessao, ALERTA_MICRO_REAIS)
    assert pode_chamar_llm(sessao)
    assert pode_chamar_llm(sessao)

    alertas = sessao.scalars(
        select(Incidente).where(Incidente.tipo == "custo_perto_do_teto")
    ).all()
    assert len(alertas) == 1
    assert alertas[0].gravidade == "alta"


def test_conversa_cara_nao_e_cortada_no_meio(
    cliente: TestClient, sessao: Session, conversa_id: uuid.UUID
) -> None:
    registrar(sessao, conversa_id, "turno", "aurora", custo_micro_reais=CUSTO_ALTO_MICRO_REAIS + 1)
    _mandar(cliente, conversa_id, "e o Seal, tem?")
    eventos = _turno(sessao, conversa_id)

    assert [nome for nome, _ in eventos if nome == "mensagem_fim"] == ["mensagem_fim"]
    sessao.expire_all()
    conversa = sessao.get(Conversa, conversa_id)
    assert conversa is not None and conversa.modo == "aurora"
    incidente = sessao.scalars(
        select(Incidente).where(Incidente.tipo == "custo_alto")
    ).one()
    assert incidente.gravidade == "baixa"


def test_a_marca_de_custo_alto_nao_se_repete_na_mesma_conversa(
    cliente: TestClient, sessao: Session, conversa_id: uuid.UUID
) -> None:
    registrar(sessao, conversa_id, "turno", "aurora", custo_micro_reais=CUSTO_ALTO_MICRO_REAIS + 1)
    for texto in ("e o Seal?", "e o preço?"):
        _mandar(cliente, conversa_id, texto)
        _turno(sessao, conversa_id)

    assert len(sessao.scalars(select(Incidente).where(Incidente.tipo == "custo_alto")).all()) == 1


def test_nenhum_span_guarda_telefone_em_claro(sessao: Session) -> None:
    registrar(
        sessao,
        None,
        "tool",
        "transferir_para_humano",
        dados={
            "argumentos": {"observacao": "ligar em +5583988714471"},
            "retorno": ["83 98871-4471"],
        },
    )
    linha = sessao.scalars(select(Trilha)).one()
    assert "988714471" not in str(linha.dados)
    assert "[TELEFONE-REMOVIDO]" in str(linha.dados)


def test_a_mensagem_do_teto_nao_e_gravada_como_gerada_por_ia(
    cliente: TestClient, sessao: Session, conversa_id: uuid.UUID
) -> None:
    _gastar(sessao, TETO_MICRO_REAIS)
    _mandar(cliente, conversa_id, "oi")
    _turno(sessao, conversa_id)

    saida = sessao.scalars(
        select(Mensagem).where(
            Mensagem.conversa_id == conversa_id, Mensagem.direcao == "saida"
        )
    ).one()
    assert saida.gerada_por_ia is False


def test_saude_responde_com_o_estado_das_dependencias(cliente: TestClient) -> None:
    assert cliente.get("/health").json() == {"status": "ok"}
    pronto = cliente.get("/health/ready")
    assert pronto.status_code == 200
    assert pronto.json()["postgres"] == "ok"
    # Sem chave no ambiente de teste: o readiness diz isso em vez de fingir que está de pé.
    assert pronto.json()["openrouter"] == "nao_configurado"
