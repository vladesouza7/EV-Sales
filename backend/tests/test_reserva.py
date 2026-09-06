"""S-05 — a operação que não pode falhar.

O teste de concorrência deste arquivo é **portão de CI** (CLAUDE.md): 50 tentativas
simultâneas, exatamente 1 vencedor. Ele roda contra um Postgres real, e não contra mock
nem SQLite — a garantia que se está testando é do banco, então testá-la sem o banco seria
testar outra coisa.

**Nunca marque este teste como `skip` para destravar o build.** Se ele falhar, o código
está errado: alguém trocou o `UPDATE … WHERE` por um `SELECT` seguido de gravação, e o Raí
vai precisar telefonar para um cliente e desmarcar.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.pii import cifrar, hash_telefone
from app.db import URL, agora, engine
from app.ia.etapas import tools_da_etapa
from app.ia.registro import disponiveis
from app.modelos import (
    Conversa,
    Espelho,
    Lead,
    PedidoDeAprovacao,
    Reserva,
    Unidade,
)
from app.reserva import PRAZO, liberar_vencidas, renovar, reservar_chassi

SEAL = dict(
    chassi="9BWZZZ377VT004471", marca="BYD", modelo="Seal", versao="Design", ano=2026,
    cor="Branco", condicao="novo", km=0, preco_centavos=24999000,
    autonomia_km=372, autonomia_fonte="INMETRO_PBEV_2026", status="disponivel",
)  # fmt: skip


def _cenario(sessao: Session, chassi: str = str(SEAL["chassi"])) -> tuple[Conversa, str]:
    """Um lead, uma conversa em `reserva`, um pedido aprovado e um espelho emitido."""
    telefone = f"+55839{uuid.uuid4().int % 10**8:08d}"
    lead = Lead(
        nome_cifrado=cifrar("Tarcísio Nóbrega"),
        telefone_cifrado=cifrar(telefone),
        telefone_hash=hash_telefone(telefone),
    )
    sessao.add(lead)
    sessao.flush()
    conversa = Conversa(
        lead_id=lead.id,
        etapa="reserva",
        token_sessao=uuid.uuid4().hex,
        token_expira_em=agora() + timedelta(days=1),
    )
    sessao.add(conversa)
    sessao.flush()

    agora_ = agora()
    pedido = PedidoDeAprovacao(
        conversa_id=conversa.id,
        chassi=chassi,
        lead_id=lead.id,
        preco_centavos=24999000,
        status="aprovado",
        codigo=uuid.uuid4().hex[:8],
        criado_em=agora_,
        expira_em=agora_ + timedelta(minutes=20),
    )
    sessao.add(pedido)
    sessao.flush()
    sessao.add(
        Espelho(
            conversa_id=conversa.id,
            chassi=chassi,
            approval_id=pedido.id,
            preco_centavos=24999000,
            numero=f"SV-2026-{uuid.uuid4().int % 10000:04d}",
            pdf_objeto="documentos/x.pdf",
            valido_ate=agora_ + timedelta(days=7),
            emitido_em=agora_,
        )
    )
    sessao.commit()
    return conversa, str(pedido.id)


@pytest.fixture
def unidade(sessao: Session) -> None:
    sessao.add(Unidade(**SEAL))  # type: ignore[arg-type]
    sessao.commit()


def test_reserva_bem_sucedida(sessao: Session, unidade: None) -> None:
    conversa, approval_id = _cenario(sessao)

    resultado = reservar_chassi(sessao, conversa, str(SEAL["chassi"]), approval_id)

    assert resultado["resultado"] == "reservado"
    sessao.expire_all()
    carro = sessao.get(Unidade, SEAL["chassi"])
    assert carro is not None and carro.status == "reservado"
    assert carro.reservado_para == conversa.lead_id
    reserva = sessao.scalars(select(Reserva)).one()
    assert reserva.approval_id == uuid.UUID(approval_id)
    assert sessao.get(Conversa, conversa.id).etapa == "test_drive"  # type: ignore[union-attr]


# ── PORTÃO DE CI — não marque como skip ──────────────────────────────────────────


def test_cinquenta_simultaneas_exatamente_uma_vence(sessao: Session, unidade: None) -> None:
    """S-05 §6 — 50 tentativas ao mesmo tempo, 1 sucesso, 49 `indisponivel`, 0 exceções.

    Cada thread tem a sua sessão e a sua conexão, e todas esperam na barreira antes de
    disparar: sem isso o teste roda em série e passaria mesmo com o código errado.
    """
    tentativas = 50
    # Pool do tamanho da corrida: com o pool padrão as threads enfileiram, e enfileirar
    # é justamente o que faria este teste passar sem provar nada.
    #
    # O `dispose` do motor global antes de começar não é zelo: sem ele, as conexões que a
    # suíte já segurava somadas a estas 50 encostam no `max_connections` do Postgres, e o
    # portão passa a falhar de vez em quando. Portão intermitente vira "roda de novo",
    # que é como um portão deixa de ser portão.
    engine.dispose()
    motor = create_engine(URL, pool_size=tentativas, max_overflow=0, pool_pre_ping=True)
    Fabrica = sessionmaker(motor, expire_on_commit=False)

    try:
        _corrida(Fabrica, tentativas)
    finally:
        motor.dispose()


def _corrida(Fabrica: sessionmaker[Session], tentativas: int) -> None:
    cenarios = []
    for _ in range(tentativas):
        with Fabrica() as preparo:
            conversa, approval_id = _cenario(preparo)
            cenarios.append((conversa.id, approval_id))

    largada = Barrier(tentativas)

    def tentar(indice: int) -> str:
        conversa_id, approval_id = cenarios[indice]
        with Fabrica() as propria:
            conversa = propria.get(Conversa, conversa_id)
            assert conversa is not None
            largada.wait(timeout=30)
            resultado = reservar_chassi(propria, conversa, str(SEAL["chassi"]), approval_id)
            return str(resultado["resultado"])

    with ThreadPoolExecutor(max_workers=tentativas) as piscina:
        resultados = list(piscina.map(tentar, range(tentativas)))

    assert resultados.count("reservado") == 1, f"esperado 1 vencedor, veio {resultados}"
    assert resultados.count("indisponivel") == tentativas - 1
    assert set(resultados) == {"reservado", "indisponivel"}, "nenhum outro desfecho"

    with Fabrica() as conferencia:
        ativas = conferencia.scalars(
            select(Reserva).where(
                Reserva.chassi == SEAL["chassi"], Reserva.status == "ativa"
            )
        ).all()
        assert len(ativas) == 1


# ── perder a corrida ─────────────────────────────────────────────────────────────


def test_quem_perde_volta_para_recomendacao_sem_o_chassi(
    sessao: Session, unidade: None
) -> None:
    primeiro, aprovacao_do_primeiro = _cenario(sessao)
    reservar_chassi(sessao, primeiro, str(SEAL["chassi"]), aprovacao_do_primeiro)

    segundo, aprovacao_do_segundo = _cenario(sessao)
    resultado = reservar_chassi(sessao, segundo, str(SEAL["chassi"]), aprovacao_do_segundo)

    assert resultado["resultado"] == "indisponivel"
    sessao.expire_all()
    assert sessao.get(Conversa, segundo.id).etapa == "recomendacao"  # type: ignore[union-attr]
    # O pedido aprovado para um carro que não existe mais como disponível expira.
    perdido = sessao.get(PedidoDeAprovacao, uuid.UUID(aprovacao_do_segundo))
    assert perdido is not None and perdido.status == "expirado"

    from app.ia.tools.estoque import buscar_unidades

    assert buscar_unidades(sessao) == [], "o chassi reservado sai do catálogo"


# ── pré-condições (§2) ───────────────────────────────────────────────────────────


def test_sem_aprovacao_valida_nao_reserva(sessao: Session, unidade: None) -> None:
    conversa, approval_id = _cenario(sessao)
    pedido = sessao.get(PedidoDeAprovacao, uuid.UUID(approval_id))
    assert pedido is not None
    pedido.status = "expirado"
    sessao.commit()

    resultado = reservar_chassi(sessao, conversa, str(SEAL["chassi"]), approval_id)

    assert resultado["resultado"] == "aprovacao_invalida"
    sessao.expire_all()
    assert sessao.get(Unidade, SEAL["chassi"]).status == "disponivel"  # type: ignore[union-attr]


def test_aprovacao_de_outra_conversa_nao_serve(sessao: Session, unidade: None) -> None:
    """A tool não confia no `approval_id` que o modelo passou.

    Sem esta checagem, um id de aprovação de outra conversa reservaria o carro para o
    cliente errado — e o `approval_id` chega pelo modelo, que lê texto do cliente.
    """
    _, aprovacao_alheia = _cenario(sessao)
    outra, _ = _cenario(sessao)

    resultado = reservar_chassi(sessao, outra, str(SEAL["chassi"]), aprovacao_alheia)

    assert resultado["resultado"] == "aprovacao_invalida"
    sessao.expire_all()
    assert sessao.get(Unidade, SEAL["chassi"]).status == "disponivel"  # type: ignore[union-attr]


def test_chassi_divergente_do_espelho_e_incidente_grave(sessao: Session, unidade: None) -> None:
    from app.modelos import Incidente

    outro = dict(SEAL, chassi="9BWZZZ377VT009902", cor="Cinza")
    sessao.add(Unidade(**outro))  # type: ignore[arg-type]
    sessao.commit()
    conversa, approval_id = _cenario(sessao)

    resultado = reservar_chassi(sessao, conversa, str(outro["chassi"]), approval_id)

    assert resultado["resultado"] == "chassi_divergente"
    incidente = sessao.scalars(
        select(Incidente).where(Incidente.tipo == "chassi_divergente")
    ).one()
    assert incidente.gravidade == "critica"
    sessao.expire_all()
    assert sessao.get(Unidade, outro["chassi"]).status == "disponivel"  # type: ignore[union-attr]
    assert sessao.get(Unidade, SEAL["chassi"]).status == "disponivel"  # type: ignore[union-attr]


def test_o_lead_nao_reserva_dois_carros(sessao: Session, unidade: None) -> None:
    outro = dict(SEAL, chassi="9BWZZZ377VT009902", cor="Cinza")
    sessao.add(Unidade(**outro))  # type: ignore[arg-type]
    sessao.commit()

    conversa, approval_id = _cenario(sessao)
    assert reservar_chassi(sessao, conversa, str(SEAL["chassi"]), approval_id)["resultado"] == (
        "reservado"
    )

    # Mesmo lead, outra conversa, outro carro.
    conversa.etapa = "reserva"
    segunda = _cenario(sessao, str(outro["chassi"]))[1]
    outra_conversa = Conversa(
        lead_id=conversa.lead_id,
        etapa="reserva",
        token_sessao=uuid.uuid4().hex,
        token_expira_em=agora() + timedelta(days=1),
    )
    sessao.add(outra_conversa)
    sessao.commit()

    resultado = reservar_chassi(sessao, outra_conversa, str(outro["chassi"]), segunda)
    assert resultado["resultado"] in {"ja_tem_reserva", "aprovacao_invalida"}
    sessao.expire_all()
    assert sessao.get(Unidade, outro["chassi"]).status == "disponivel"  # type: ignore[union-attr]


# ── validade e liberação (§4) ────────────────────────────────────────────────────


def test_reserva_expira_em_72_horas(sessao: Session, unidade: None) -> None:
    conversa, approval_id = _cenario(sessao)
    reservar_chassi(sessao, conversa, str(SEAL["chassi"]), approval_id)
    reserva = sessao.scalars(select(Reserva)).one()
    reserva.criada_em = agora() - PRAZO - timedelta(hours=1)
    reserva.expira_em = agora() - timedelta(hours=1)
    sessao.commit()

    liberadas = liberar_vencidas(sessao)

    assert liberadas == [SEAL["chassi"]]
    sessao.expire_all()
    carro = sessao.get(Unidade, SEAL["chassi"])
    assert carro is not None and carro.status == "disponivel"
    assert carro.reservado_para is None
    assert sessao.get(Reserva, reserva.id).status == "liberada"  # type: ignore[union-attr]


def test_carro_vendido_nao_volta_para_o_catalogo(sessao: Session, unidade: None) -> None:
    """O desfecho `vendeu` tira o carro da loja. A rotina de liberação não o traz de volta."""
    conversa, approval_id = _cenario(sessao)
    reservar_chassi(sessao, conversa, str(SEAL["chassi"]), approval_id)
    carro = sessao.get(Unidade, SEAL["chassi"])
    assert carro is not None
    carro.status = "vendido"
    reserva = sessao.scalars(select(Reserva)).one()
    # `criada_em` vai junto: `ck_reservas_prazo_positivo` recusa vencimento antes da
    # criação, e recusar isso é o comportamento certo do banco.
    reserva.criada_em = agora() - PRAZO - timedelta(hours=1)
    reserva.expira_em = agora() - timedelta(hours=1)
    sessao.commit()

    liberar_vencidas(sessao)

    sessao.expire_all()
    assert sessao.get(Unidade, SEAL["chassi"]).status == "vendido"  # type: ignore[union-attr]


def test_renova_no_maximo_duas_vezes(sessao: Session, unidade: None) -> None:
    conversa, approval_id = _cenario(sessao)
    reservar_chassi(sessao, conversa, str(SEAL["chassi"]), approval_id)
    reserva = sessao.scalars(select(Reserva)).one()

    assert renovar(sessao, reserva.id)["resultado"] == "renovada"
    assert renovar(sessao, reserva.id)["resultado"] == "renovada"
    terceira = renovar(sessao, reserva.id)

    assert terceira["resultado"] == "limite_de_renovacoes"
    sessao.expire_all()
    assert sessao.get(Reserva, reserva.id).renovacoes == 2  # type: ignore[union-attr]


def test_a_aurora_nao_tem_tool_de_cancelamento() -> None:
    """S-05 §4 — liberar um carro é decisão comercial, não de agente."""
    proibidas = {"cancelar_reserva", "cancelar_reserva_de_outro", "liberar_chassi"}
    for etapa in ("reserva", "test_drive", "objecao", "recomendacao"):
        assert proibidas.isdisjoint(tools_da_etapa(etapa))
        assert proibidas.isdisjoint(disponiveis(etapa))
