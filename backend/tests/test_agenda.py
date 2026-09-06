"""S-07 — critérios de aceite da agenda de test drive.

Fora deste arquivo, por não estarem implementados: lembrete de 24h (§6), dossiê do
vendedor (§8) e registro de desfecho (§9).
"""

import logging
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agenda import (
    DURACAO,
    HorarioIndisponivel,
    agendar,
    horarios_disponiveis,
    inicios_do_dia,
)
from app.core.pii import cifrar, decifrar
from app.db import FUSO
from app.modelos import AgendaBloqueio, Conversa, Lead, TestDrive, Unidade, Vendedor

LEAD = {"nome": "Almir", "telefone": "(83) 98871-4471"}

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
DOLPHIN = dict(SEAL, chassi="9BWZZZ377VT100002", modelo="Dolphin", cor="Prata")

# 2026-09-09 é uma quarta-feira. Às 8h, com 2h de antecedência, a grade abre às 10h.
QUARTA_CEDO = datetime(2026, 9, 9, 8, 0, tzinfo=FUSO)


@pytest.fixture
def equipe(sessao: Session) -> tuple[Vendedor, Vendedor]:
    tarcisio = Vendedor(nome="Tarcísio", telefone_cifrado=cifrar("+5583988710001"))
    jaqueline = Vendedor(nome="Jaqueline", telefone_cifrado=cifrar("+5583988710002"))
    sessao.add_all([tarcisio, jaqueline])
    sessao.commit()
    return tarcisio, jaqueline


@pytest.fixture
def seal(sessao: Session) -> Unidade:
    unidade = Unidade(**SEAL)
    sessao.add(unidade)
    sessao.commit()
    return unidade


def _conversa(sessao: Session, cliente: TestClient) -> Conversa:
    cliente.post("/api/leads", json=LEAD)
    return sessao.scalars(select(Conversa)).one()


def _ocupar(
    sessao: Session, vendedor: Vendedor, inicio: datetime, chassi: str, lead_id: object = None
) -> TestDrive:
    """Grava direto: aqui o interesse é o efeito na agenda, não o caminho do agendamento."""
    lead = sessao.scalars(select(Lead)).first()
    conversa = sessao.scalars(select(Conversa)).first()
    assert lead is not None and conversa is not None
    ocupacao = TestDrive(
        conversa_id=conversa.id,
        lead_id=lead.id,
        chassi=chassi,
        vendedor_id=vendedor.id,
        inicio=inicio,
        fim=inicio + DURACAO,
    )
    sessao.add(ocupacao)
    sessao.commit()
    return ocupacao


# ── só horários reais são oferecidos ─────────────────────────────────────────────


def test_vendedor_ocupado_e_vendedora_de_folga_somem_da_agenda(
    cliente: TestClient, sessao: Session, equipe: tuple[Vendedor, Vendedor], seal: Unidade
) -> None:
    tarcisio, jaqueline = equipe
    _conversa(sessao, cliente)
    quatorze = QUARTA_CEDO.replace(hour=14)
    _ocupar(sessao, tarcisio, quatorze, SEAL["chassi"])  # type: ignore[arg-type]
    sessao.add(
        AgendaBloqueio(
            vendedor_id=jaqueline.id,
            inicio=QUARTA_CEDO.replace(hour=0),
            fim=QUARTA_CEDO.replace(hour=23),
            motivo="folga",
        )
    )
    sessao.commit()

    opcoes = horarios_disponiveis(
        sessao, chassi=SEAL["chassi"], dia=date(2026, 9, 9), referencia=QUARTA_CEDO  # type: ignore[arg-type]
    )

    assert opcoes, "a quarta inteira não pode ter sumido"
    assert all(vendedor.nome != "Jaqueline" for _, vendedor in opcoes)
    assert all(inicio != quatorze for inicio, _ in opcoes)


# ── no máximo 3 opções ───────────────────────────────────────────────────────────


def test_no_maximo_tres_opcoes_e_a_primeira_e_a_mais_proxima(
    sessao: Session, equipe: tuple[Vendedor, Vendedor], seal: Unidade
) -> None:
    opcoes = horarios_disponiveis(
        sessao, chassi=SEAL["chassi"], referencia=QUARTA_CEDO  # type: ignore[arg-type]
    )

    assert len(opcoes) == 3
    assert opcoes[0][0] == QUARTA_CEDO.replace(hour=10)
    assert [inicio for inicio, _ in opcoes] == sorted(inicio for inicio, _ in opcoes)


# ── o mesmo carro não sai duas vezes na mesma hora ───────────────────────────────


def test_o_banco_recusa_o_mesmo_chassi_no_mesmo_horario(
    cliente: TestClient, sessao: Session, equipe: tuple[Vendedor, Vendedor], seal: Unidade
) -> None:
    tarcisio, jaqueline = equipe
    conversa = _conversa(sessao, cliente)
    quatorze = QUARTA_CEDO.replace(hour=14)
    _ocupar(sessao, tarcisio, quatorze, SEAL["chassi"])  # type: ignore[arg-type]

    with pytest.raises(HorarioIndisponivel):
        agendar(
            sessao,
            conversa=conversa,
            lead_id=conversa.lead_id,
            chassi=SEAL["chassi"],  # type: ignore[arg-type]
            inicio=quatorze,
            referencia=QUARTA_CEDO,
        )

    assert len(sessao.scalars(select(TestDrive)).all()) == 1
    assert jaqueline.nome  # ela estava livre, e ainda assim o carro não sai duas vezes


def test_a_constraint_e_do_banco_nao_do_python(
    cliente: TestClient, sessao: Session, equipe: tuple[Vendedor, Vendedor], seal: Unidade
) -> None:
    """Sem passar pela conferência: o INSERT direto tem que quicar no índice."""
    tarcisio, jaqueline = equipe
    _conversa(sessao, cliente)
    quatorze = QUARTA_CEDO.replace(hour=14)
    _ocupar(sessao, tarcisio, quatorze, SEAL["chassi"])  # type: ignore[arg-type]

    with pytest.raises(IntegrityError):
        _ocupar(sessao, jaqueline, quatorze, SEAL["chassi"])  # type: ignore[arg-type]
    sessao.rollback()


def test_vendedor_nao_atende_dois_carros_na_mesma_hora(
    cliente: TestClient, sessao: Session, equipe: tuple[Vendedor, Vendedor], seal: Unidade
) -> None:
    tarcisio, _ = equipe
    _conversa(sessao, cliente)
    sessao.add(Unidade(**DOLPHIN))
    sessao.commit()
    quatorze = QUARTA_CEDO.replace(hour=14)
    _ocupar(sessao, tarcisio, quatorze, SEAL["chassi"])  # type: ignore[arg-type]

    with pytest.raises(IntegrityError):
        _ocupar(sessao, tarcisio, quatorze, DOLPHIN["chassi"])  # type: ignore[arg-type]
    sessao.rollback()


# ── janela de atendimento ────────────────────────────────────────────────────────


def test_domingo_nao_tem_horario(
    sessao: Session, equipe: tuple[Vendedor, Vendedor], seal: Unidade
) -> None:
    assert (
        horarios_disponiveis(
            sessao,
            chassi=SEAL["chassi"],  # type: ignore[arg-type]
            dia=date(2026, 9, 13),
            referencia=QUARTA_CEDO,
        )
        == []
    )


def test_sabado_fecha_as_treze(
    sessao: Session, equipe: tuple[Vendedor, Vendedor], seal: Unidade
) -> None:
    opcoes = horarios_disponiveis(
        sessao,
        chassi=SEAL["chassi"],  # type: ignore[arg-type]
        dia=date(2026, 9, 12),
        referencia=QUARTA_CEDO,
    )

    assert [inicio.hour for inicio, _ in opcoes] == [9, 10, 11]


def test_almoco_e_bloqueado(
    sessao: Session, equipe: tuple[Vendedor, Vendedor], seal: Unidade
) -> None:
    """A grade da quinta inteira, sem limite de 3, não pode ter nada às 12h."""
    assert 12 not in [inicio.hour for inicio in inicios_do_dia(date(2026, 9, 10))]
    assert [inicio.hour for inicio in inicios_do_dia(date(2026, 9, 10))] == [
        9, 10, 11, 13, 14, 15, 16, 17
    ]  # fmt: skip


# ── antecedência ─────────────────────────────────────────────────────────────────


def test_antecedencia_minima_de_duas_horas(
    sessao: Session, equipe: tuple[Vendedor, Vendedor], seal: Unidade
) -> None:
    quinta_treze = datetime(2026, 9, 10, 13, 0, tzinfo=FUSO)

    opcoes = horarios_disponiveis(
        sessao,
        chassi=SEAL["chassi"],  # type: ignore[arg-type]
        dia=date(2026, 9, 10),
        referencia=quinta_treze,
    )

    assert opcoes[0][0].hour >= 15


def test_antecedencia_maxima_de_quatorze_dias(
    sessao: Session, equipe: tuple[Vendedor, Vendedor], seal: Unidade
) -> None:
    longe = (QUARTA_CEDO + timedelta(days=20)).date()

    assert (
        horarios_disponiveis(
            sessao,
            chassi=SEAL["chassi"],  # type: ignore[arg-type]
            dia=longe,
            referencia=QUARTA_CEDO,
        )
        == []
    )


# ── distribuição entre vendedores ────────────────────────────────────────────────


def test_quem_tem_menos_agendamento_leva_o_proximo(
    cliente: TestClient, sessao: Session, equipe: tuple[Vendedor, Vendedor], seal: Unidade
) -> None:
    tarcisio, _ = equipe
    _conversa(sessao, cliente)
    sessao.add(Unidade(**DOLPHIN))
    sessao.commit()
    # Tarcísio com 3 na semana, Jaqueline com nenhuma.
    for hora in (14, 15, 16):
        _ocupar(sessao, tarcisio, QUARTA_CEDO.replace(hour=hora), DOLPHIN["chassi"])  # type: ignore[arg-type]

    opcoes = horarios_disponiveis(
        sessao, chassi=SEAL["chassi"], referencia=QUARTA_CEDO  # type: ignore[arg-type]
    )

    assert opcoes[0][1].nome == "Jaqueline"


def test_empate_resolve_por_ordem_alfabetica(
    sessao: Session, equipe: tuple[Vendedor, Vendedor], seal: Unidade
) -> None:
    opcoes = horarios_disponiveis(
        sessao, chassi=SEAL["chassi"], referencia=QUARTA_CEDO  # type: ignore[arg-type]
    )

    assert opcoes[0][1].nome == "Jaqueline"  # J vem antes de T


# ── chassi ───────────────────────────────────────────────────────────────────────


def test_carro_vendido_nao_tem_test_drive(
    sessao: Session, equipe: tuple[Vendedor, Vendedor], seal: Unidade
) -> None:
    seal.status = "vendido"
    sessao.commit()

    assert (
        horarios_disponiveis(
            sessao, chassi=SEAL["chassi"], referencia=QUARTA_CEDO  # type: ignore[arg-type]
        )
        == []
    )


# ── a página pública ─────────────────────────────────────────────────────────────


def test_agendamento_pela_pagina_cria_lead_cifrado_e_test_drive(
    cliente: TestClient, sessao: Session, equipe: tuple[Vendedor, Vendedor], seal: Unidade
) -> None:
    opcoes = cliente.get("/api/agenda", params={"chassi": SEAL["chassi"]}).json()
    assert len(opcoes) <= 3 and opcoes

    resposta = cliente.post(
        "/api/test-drives",
        json={**LEAD, "chassi": SEAL["chassi"], "inicio": opcoes[0]["inicio"]},
    )

    assert resposta.status_code == 201
    corpo = resposta.json()
    assert corpo["vendedor"] == opcoes[0]["vendedor"]
    assert corpo["quando"] == opcoes[0]["rotulo"]
    assert "Epitácio Pessoa" in corpo["endereco"]

    lead = sessao.scalars(select(Lead)).one()
    assert decifrar(lead.nome_cifrado) == "Almir"
    assert lead.origem == "test_drive"

    marcado = sessao.scalars(select(TestDrive)).one()
    assert marcado.lead_id == lead.id
    assert marcado.fim - marcado.inicio == DURACAO

    conversa = sessao.get(Conversa, marcado.conversa_id)
    assert conversa is not None
    assert conversa.etapa == "encerrada"
    assert conversa.desfecho == "test_drive_agendado"


def test_horario_fora_da_grade_e_recusado(
    cliente: TestClient, sessao: Session, equipe: tuple[Vendedor, Vendedor], seal: Unidade
) -> None:
    opcoes = cliente.get("/api/agenda", params={"chassi": SEAL["chassi"]}).json()
    quebrado = datetime.fromisoformat(opcoes[0]["inicio"]) + timedelta(minutes=20)

    resposta = cliente.post(
        "/api/test-drives",
        json={**LEAD, "chassi": SEAL["chassi"], "inicio": quebrado.isoformat()},
    )

    assert resposta.status_code == 409
    assert sessao.scalars(select(TestDrive)).all() == []


def test_pagina_de_test_drive_nao_loga_telefone(
    cliente: TestClient,
    equipe: tuple[Vendedor, Vendedor],
    seal: Unidade,
    caplog: pytest.LogCaptureFixture,
) -> None:
    opcoes = cliente.get("/api/agenda", params={"chassi": SEAL["chassi"]}).json()

    with caplog.at_level(logging.DEBUG):
        cliente.post(
            "/api/test-drives",
            json={**LEAD, "chassi": SEAL["chassi"], "inicio": opcoes[0]["inicio"]},
        )

    assert "988714471" not in caplog.text
    assert "(83) *****-4471" in caplog.text
