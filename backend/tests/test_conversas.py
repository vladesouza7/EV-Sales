"""S-02 — critérios de aceite do chat web e da sessão de conversa."""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import cast

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.conversas import _eventos
from app.db import agora
from app.ia import turno as modulo_turno
from app.ia.etapas import tools_da_etapa
from app.ia.turno import Evento, executar_turno
from app.ia.verificacao import Veredito
from app.modelos import Conversa, Mensagem, Unidade

from .dubles import TEXTO_PADRAO, ProvedorDuble

LEAD = {"nome": "Jaqueline", "telefone": "(83) 98871-4471", "origem": "landing"}

SEAL = dict(
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
    status="disponivel",
)


@pytest.fixture
def conversa_id(cliente: TestClient) -> uuid.UUID:
    """O cookie de sessão fica no cliente: é assim que a S-01 entrega a conversa."""
    return uuid.UUID(cliente.post("/api/leads", json=LEAD).json()["conversa_id"])


def _drenar(gerador: AsyncIterator[Evento]) -> list[Evento]:
    """Roda o turno inteiro. Sem plugin de asyncio: `asyncio.run` já faz o trabalho."""

    async def rodar() -> list[Evento]:
        return [evento async for evento in gerador]

    return asyncio.run(rodar())


def _turno(sessao: Session, conversa_id: uuid.UUID) -> list[Evento]:
    sessao.expire_all()
    conversa = sessao.get(Conversa, conversa_id)
    assert conversa is not None
    entrada = sessao.scalars(
        select(Mensagem)
        .where(Mensagem.conversa_id == conversa_id, Mensagem.processada_em.is_(None))
        .order_by(Mensagem.criada_em, Mensagem.id)
        .limit(1)
    ).one()
    return _drenar(executar_turno(sessao, conversa, entrada))


class _AbaAberta:
    """Dublê do `Request`: o gerador do stream só pergunta se o cliente ainda está lá.

    O `TestClient` roda o app ASGI até o fim antes de devolver a resposta, então um SSE
    que fica aberto — que é o comportamento certo em produção — travaria o teste. Aqui a
    aba fecha depois de N verificações, e o gerador é exercitado inteiro.
    """

    def __init__(self, verificacoes: int) -> None:
        self.restantes = verificacoes

    async def is_disconnected(self) -> bool:
        self.restantes -= 1
        return self.restantes < 0


def _abrir_stream(
    conversa_id: uuid.UUID, verificacoes: int = 3, ultimo: str | None = None
) -> list[Evento]:
    aba = cast(Request, _AbaAberta(verificacoes))

    async def rodar() -> list[str]:
        return [pedaco async for pedaco in _eventos(conversa_id, aba, ultimo)]

    return _analisar(asyncio.run(rodar()))


def _analisar(pedacos: list[str]) -> list[Evento]:
    """SSE de volta para eventos. `: ping` não é evento e não entra."""
    eventos: list[Evento] = []
    for pedaco in pedacos:
        nome = ""
        for linha in pedaco.splitlines():
            if linha.startswith("event: "):
                nome = linha.removeprefix("event: ")
            elif linha.startswith("data: ") and nome:
                eventos.append((nome, json.loads(linha.removeprefix("data: "))))
    return eventos


def _nomes(eventos: list[Evento]) -> list[str]:
    return [nome for nome, _ in eventos]


def _saidas(sessao: Session, conversa_id: uuid.UUID) -> list[Mensagem]:
    return list(
        sessao.scalars(
            select(Mensagem)
            .where(Mensagem.conversa_id == conversa_id, Mensagem.direcao == "saida")
            .order_by(Mensagem.criada_em)
        )
    )


# ── resposta chega em streaming ──────────────────────────────────────────────────


def test_resposta_chega_em_streaming(
    cliente: TestClient, sessao: Session, conversa_id: uuid.UUID
) -> None:
    cliente.post(
        f"/api/conversas/{conversa_id}/mensagens",
        json={"conteudo": "quero um elétrico até 150 mil"},
    )

    eventos = _abrir_stream(conversa_id)

    assert _nomes(eventos).count("token") > 1, "os tokens chegam progressivamente"
    assert _nomes(eventos)[-1] == "mensagem_fim"
    assert eventos[-1][1]["etapa"] == "qualificacao"

    sessao.expire_all()
    saida = _saidas(sessao, conversa_id)
    assert len(saida) == 1
    assert saida[0].gerada_por_ia is True
    assert saida[0].autor == "aurora"
    inteiro = "".join(str(d["texto"]) for n, d in eventos if n == "token")
    assert inteiro.strip() == saida[0].conteudo


# ── a consulta ao estoque fica visível para o cliente ────────────────────────────


def test_consulta_ao_estoque_fica_visivel(
    cliente: TestClient, sessao: Session, conversa_id: uuid.UUID, provedor: ProvedorDuble
) -> None:
    provedor.chamar_tool("buscar_unidades")
    provedor.responder("Tenho um Seal branco aqui na loja, quer ver?")
    sessao.add(Unidade(**SEAL))
    conversa = sessao.get(Conversa, conversa_id)
    assert conversa is not None
    conversa.etapa = "recomendacao"
    sessao.commit()

    cliente.post(f"/api/conversas/{conversa_id}/mensagens", json={"conteudo": "me mostra opções"})
    eventos = _turno(sessao, conversa_id)

    nomes = _nomes(eventos)
    assert nomes.index("tool_inicio") < nomes.index("tool_fim") < nomes.index("token")
    inicio = next(d for n, d in eventos if n == "tool_inicio")
    fim = next(d for n, d in eventos if n == "tool_fim")
    assert inicio["nome"] == "buscar_unidades"
    assert fim == {"nome": "buscar_unidades", "resumo": "1 unidades"}


# ── nenhum texto não verificado chega ao cliente ─────────────────────────────────


def test_texto_reprovado_nao_chega_ao_cliente(
    cliente: TestClient,
    sessao: Session,
    conversa_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reprovado = Veredito(aprovado=False, extraidos=["R$ 1.000"], divergentes=["R$ 1.000"])
    monkeypatch.setattr(
        modulo_turno, "verificar_numeros", lambda texto, permitidos, do_cliente: reprovado
    )

    cliente.post(f"/api/conversas/{conversa_id}/mensagens", json={"conteudo": "qual o preço?"})
    eventos = _turno(sessao, conversa_id)

    assert "token" not in _nomes(eventos)
    assert _nomes(eventos) == ["erro"]
    assert eventos[0][1]["codigo"] == "numero_divergente"
    assert TEXTO_PADRAO not in json.dumps(eventos[0][1])

    sessao.expire_all()
    assert _saidas(sessao, conversa_id) == []
    conversa = sessao.get(Conversa, conversa_id)
    assert conversa is not None and conversa.modo == "humano"


# ── dois envios simultâneos não geram dois turnos ────────────────────────────────


def test_dois_envios_nao_geram_dois_turnos(
    cliente: TestClient, sessao: Session, conversa_id: uuid.UUID
) -> None:
    cliente.post(f"/api/conversas/{conversa_id}/mensagens", json={"conteudo": "primeira"})
    cliente.post(f"/api/conversas/{conversa_id}/mensagens", json={"conteudo": "segunda"})

    _turno(sessao, conversa_id)

    sessao.expire_all()
    assert len(_saidas(sessao, conversa_id)) == 1
    pendentes = list(
        sessao.scalars(
            select(Mensagem).where(
                Mensagem.conversa_id == conversa_id, Mensagem.processada_em.is_(None)
            )
        )
    )
    assert [m.conteudo for m in pendentes] == ["segunda"]

    _turno(sessao, conversa_id)
    sessao.expire_all()
    assert len(_saidas(sessao, conversa_id)) == 2


# ── sem tools na etapa errada ────────────────────────────────────────────────────


def test_aguardando_aprovacao_nao_tem_tool_nenhuma() -> None:
    assert tools_da_etapa("aguardando_aprovacao") == ()
    assert tools_da_etapa("humano") == ()
    assert tools_da_etapa("encerrada") == ()


def test_nenhuma_etapa_oferece_tool_de_desconto() -> None:
    """Invariante 2 — a tool não existe, e nenhum mapa pode reintroduzi-la."""
    proibidas = {"aplicar_desconto", "alterar_preco", "criar_condicao_especial"}
    for etapa in ("qualificacao", "recomendacao", "objecao", "condicao", "reserva"):
        assert proibidas.isdisjoint(tools_da_etapa(etapa))


def test_transferir_para_humano_em_toda_etapa_com_turno() -> None:
    for etapa in ("saudacao", "qualificacao", "recomendacao", "objecao", "condicao", "reserva"):
        assert "transferir_para_humano" in tools_da_etapa(etapa)


# ── o histórico atravessa o canal ────────────────────────────────────────────────


def test_historico_atravessa_o_canal(
    cliente: TestClient, sessao: Session, conversa_id: uuid.UUID
) -> None:
    for numero in range(6):
        sessao.add(
            Mensagem(
                conversa_id=conversa_id,
                direcao="entrada",
                autor="cliente",
                canal="web",
                conteudo=f"mensagem {numero}",
                processada_em=agora(),
            )
        )
    conversa = sessao.get(Conversa, conversa_id)
    assert conversa is not None
    sessao.commit()

    conversa.canal_atual = "whatsapp"
    sessao.commit()

    corpo = cliente.get(f"/api/conversas/{conversa_id}").json()
    assert corpo["conversa_id"] == str(conversa_id)
    assert len(corpo["mensagens"]) == 6
    assert all(m["canal"] == "web" for m in corpo["mensagens"])


# ── limites do envio (§4) ────────────────────────────────────────────────────────


@pytest.mark.parametrize("conteudo", ["", "   ", "\n\t "])
def test_mensagem_vazia_nao_cria_turno(
    cliente: TestClient, sessao: Session, conversa_id: uuid.UUID, conteudo: str
) -> None:
    resposta = cliente.post(f"/api/conversas/{conversa_id}/mensagens", json={"conteudo": conteudo})

    assert resposta.status_code == 400
    assert sessao.scalars(select(Mensagem)).all() == []


def test_mensagem_longa_demais_e_recusada(cliente: TestClient, conversa_id: uuid.UUID) -> None:
    limite = cliente.post(f"/api/conversas/{conversa_id}/mensagens", json={"conteudo": "a" * 2000})
    passou = cliente.post(f"/api/conversas/{conversa_id}/mensagens", json={"conteudo": "a" * 2001})

    assert limite.status_code == 201
    assert passou.status_code == 400


def test_doze_mensagens_por_minuto(cliente: TestClient, conversa_id: uuid.UUID) -> None:
    for _ in range(12):
        assert (
            cliente.post(f"/api/conversas/{conversa_id}/mensagens", json={"conteudo": "oi"})
        ).status_code == 201

    excedente = cliente.post(f"/api/conversas/{conversa_id}/mensagens", json={"conteudo": "oi"})
    assert excedente.status_code == 429


def test_sessenta_turnos_passam_a_conversa_para_humano(
    cliente: TestClient, sessao: Session, conversa_id: uuid.UUID
) -> None:
    for numero in range(59):
        sessao.add(
            Mensagem(
                conversa_id=conversa_id,
                direcao="entrada",
                autor="cliente",
                canal="web",
                conteudo=f"turno {numero}",
                processada_em=agora(),
            )
        )
    sessao.commit()

    corpo = cliente.post(
        f"/api/conversas/{conversa_id}/mensagens", json={"conteudo": "turno 60"}
    ).json()

    assert corpo["modo"] == "humano"
    sessao.expire_all()
    conversa = sessao.get(Conversa, conversa_id)
    assert conversa is not None and conversa.etapa == "humano"


def test_botao_falar_com_uma_pessoa_tira_a_aurora(
    cliente: TestClient, sessao: Session, conversa_id: uuid.UUID
) -> None:
    corpo = cliente.post(f"/api/conversas/{conversa_id}/humano").json()

    assert corpo == {"modo": "humano", "etapa": "humano"}
    sessao.expire_all()
    conversa = sessao.get(Conversa, conversa_id)
    assert conversa is not None and conversa.modo == "humano"


def test_em_modo_humano_a_aurora_nao_responde(
    cliente: TestClient, sessao: Session, conversa_id: uuid.UUID
) -> None:
    cliente.post(f"/api/conversas/{conversa_id}/humano")
    cliente.post(f"/api/conversas/{conversa_id}/mensagens", json={"conteudo": "alguém aí?"})

    eventos = _abrir_stream(conversa_id)

    assert eventos == []
    sessao.expire_all()
    assert _saidas(sessao, conversa_id) == []


# ── sessão (§6) ──────────────────────────────────────────────────────────────────


def test_conversa_de_outro_cliente_nao_abre(cliente: TestClient, conversa_id: uuid.UUID) -> None:
    cliente.cookies.clear()

    assert cliente.get(f"/api/conversas/{conversa_id}").status_code == 404
    assert cliente.get(f"/api/conversas/{uuid.uuid4()}").status_code == 404
    # O stream é recusado na dependência, antes de a resposta começar.
    assert cliente.get(f"/api/conversas/{conversa_id}/stream").status_code == 404


def test_sessao_expirada_pede_novo_cadastro(
    cliente: TestClient, sessao: Session, conversa_id: uuid.UUID
) -> None:
    conversa = sessao.get(Conversa, conversa_id)
    assert conversa is not None
    conversa.token_expira_em = agora() - timedelta(minutes=1)
    sessao.commit()

    assert cliente.get(f"/api/conversas/{conversa_id}").status_code == 401


def test_reconexao_repete_o_que_o_cliente_perdeu(
    cliente: TestClient, sessao: Session, conversa_id: uuid.UUID
) -> None:
    """`Last-Event-ID` (§3): o que veio depois do último id visto é reenviado."""
    marco = Mensagem(
        conversa_id=conversa_id,
        direcao="saida",
        autor="aurora",
        canal="web",
        conteudo="primeira resposta",
        gerada_por_ia=True,
        processada_em=agora(),
    )
    sessao.add(marco)
    sessao.commit()
    perdida = Mensagem(
        conversa_id=conversa_id,
        direcao="saida",
        autor="aurora",
        canal="web",
        conteudo="a que caiu no meio",
        gerada_por_ia=True,
        criada_em=agora() + timedelta(seconds=1),
        processada_em=agora(),
    )
    sessao.add(perdida)
    sessao.commit()

    eventos = _abrir_stream(conversa_id, ultimo=str(marco.id))

    textos = [dados.get("texto") for nome, dados in eventos if nome == "token"]
    assert textos == ["a que caiu no meio"]
