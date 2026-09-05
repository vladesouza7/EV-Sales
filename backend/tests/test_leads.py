"""S-01 — critérios de aceite da captura de lead."""

import logging

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.pii import decifrar, hash_telefone
from app.modelos import Conversa, Lead

VALIDO = {"nome": "Tarcísio", "telefone": "(83) 98871-4471", "origem": "landing"}
E164 = "+5583988714471"


def test_cadastro_valido_abre_a_conversa(cliente: TestClient, sessao: Session) -> None:
    resposta = cliente.post("/api/leads", json=VALIDO)

    assert resposta.status_code == 201
    corpo = resposta.json()
    assert corpo["conversa_id"]
    assert corpo["token_sessao"]

    lead = sessao.scalars(select(Lead)).one()
    assert decifrar(lead.nome_cifrado) == "Tarcísio"
    assert decifrar(lead.telefone_cifrado) == E164
    assert lead.telefone_hash == hash_telefone(E164)

    conversa = sessao.scalars(select(Conversa)).one()
    assert conversa.etapa == "saudacao"
    assert conversa.lead_id == lead.id


def test_cookie_de_sessao_e_httponly_e_lax(cliente: TestClient) -> None:
    resposta = cliente.post("/api/leads", json=VALIDO)
    bruto = resposta.headers["set-cookie"].lower()
    assert "httponly" in bruto
    assert "samesite=lax" in bruto


def test_telefone_fixo_e_recusado_com_orientacao(cliente: TestClient, sessao: Session) -> None:
    resposta = cliente.post("/api/leads", json={**VALIDO, "telefone": "(83) 3244-1010"})

    assert resposta.status_code == 422
    assert "Precisa ser um celular com WhatsApp" in resposta.text
    assert sessao.scalars(select(Lead)).all() == []


def test_nome_com_digito_e_recusado(cliente: TestClient, sessao: Session) -> None:
    resposta = cliente.post("/api/leads", json={**VALIDO, "nome": "Tarcisio 2"})

    assert resposta.status_code == 422
    assert sessao.scalars(select(Lead)).all() == []


def test_cliente_que_volta_nao_vira_lead_duplicado(cliente: TestClient, sessao: Session) -> None:
    primeira = cliente.post("/api/leads", json=VALIDO)
    segunda = cliente.post("/api/leads", json={**VALIDO, "telefone": "+55 83 98871-4471"})

    assert primeira.status_code == segunda.status_code == 201
    assert len(sessao.scalars(select(Lead)).all()) == 1
    conversas = sessao.scalars(select(Conversa)).all()
    assert len(conversas) == 2
    assert primeira.json()["conversa_id"] != segunda.json()["conversa_id"]
    assert {c.lead_id for c in conversas} == {sessao.scalars(select(Lead)).one().id}


def test_limite_por_ip(cliente: TestClient) -> None:
    for n in range(5):
        aceito = cliente.post("/api/leads", json={**VALIDO, "telefone": f"(83) 9887{n}-4471"})
        assert aceito.status_code == 201, aceito.text

    barrado = cliente.post("/api/leads", json={**VALIDO, "telefone": "(83) 99999-0000"})
    assert barrado.status_code == 429


def test_limite_por_telefone_devolve_a_conversa_ativa(cliente: TestClient) -> None:
    for _ in range(3):
        assert cliente.post("/api/leads", json=VALIDO).status_code == 201

    barrado = cliente.post("/api/leads", json=VALIDO)
    assert barrado.status_code == 429
    assert "Você já tem uma conversa aberta com a Aurora" in barrado.text
    assert barrado.json()["detail"]["conversa_id"]


def test_pii_nao_aparece_em_log(cliente: TestClient, caplog) -> None:  # type: ignore[no-untyped-def]
    with caplog.at_level(logging.DEBUG):
        cliente.post("/api/leads", json={**VALIDO, "nome": "Tarcísio Nóbrega"})

    assert "(83) *****-4471" in caplog.text
    assert "988714471" not in caplog.text
    assert "Nóbrega" not in caplog.text
