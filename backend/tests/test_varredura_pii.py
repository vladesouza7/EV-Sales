"""S-09 §7 — a varredura. Portão de CI: 0 ocorrências.

Não é mais um teste pontual por endpoint: roda um atendimento com um telefone e um
sobrenome sintéticos conhecidos e depois procura os dois em tudo que **sai** do processo
— log da aplicação e a trilha, que é o banco de traces do ADR-006.

O corpo da resposta de `/api/leads/{id}/telefone` fica de fora de propósito: decifrar ali
é um dos três caminhos autorizados da §2. O que a varredura cobre é o descuido, não o
caminho revisado.
"""

import json
import logging
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.autenticacao import criar_usuario
from app.modelos import Conversa, Incidente, Lead, Trilha, Usuario

from .test_conversas import _turno  # o mesmo turno da S-02; duplicar seria uma segunda verdade

TELEFONE = "+5583988714471"
SOBRENOME = "Nóbrega"
_DIGITOS = TELEFONE.removeprefix("+")

# Com e sem +55, com e sem máscara de digitação. `(83) *****-4471` — a forma correta —
# não está aqui, e nenhuma destas é substring dela.
FORMAS = (
    TELEFONE,
    _DIGITOS,
    _DIGITOS[2:],
    _DIGITOS[4:],
    "98871-4471",
    "98871 4471",
    "(83) 98871-4471",
    SOBRENOME,
)


def _vazamentos(texto: str) -> list[str]:
    return sorted({forma for forma in FORMAS if forma in texto})


def test_a_varredura_pega_o_vazamento(caplog: pytest.LogCaptureFixture) -> None:
    """Portão que não reprova o código errado é decoração (S-09, último cenário)."""
    caplog.set_level(logging.DEBUG)
    logging.getLogger("evsales").info("cliente %s %s", TELEFONE, SOBRENOME)
    achados = _vazamentos(caplog.text)
    assert TELEFONE in achados and SOBRENOME in achados


def test_atendimento_completo_nao_deixa_pii_em_claro(
    cliente: TestClient, sessao: Session, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    conversa_id = uuid.UUID(
        cliente.post(
            "/api/leads",
            json={"nome": f"Tarcísio {SOBRENOME}", "telefone": TELEFONE, "origem": "landing"},
        ).json()["conversa_id"]
    )

    # O cliente escreve o próprio telefone, o CPF e o e-mail no chat — é o que ele faz.
    cliente.post(
        f"/api/conversas/{conversa_id}/mensagens",
        json={
            "conteudo": (
                f"oi, sou o Tarcísio {SOBRENOME}, meu zap é {TELEFONE}, "
                "CPF 000.000.000-00, tarcisio@email.com"
            )
        },
    )
    _turno(sessao, conversa_id)
    cliente.get(f"/api/conversas/{conversa_id}")

    # 422 de telefone inválido: o corpo de erro é uma das quatro superfícies da invariante 5.
    cliente.post("/api/leads", json={"nome": "Alguém", "telefone": f"{TELEFONE}9"})

    conversa = sessao.get(Conversa, conversa_id)
    assert conversa is not None
    lead = sessao.get(Lead, conversa.lead_id)
    assert lead is not None
    logging.getLogger("evsales").info("%s", lead)  # o descuido mais comum, S-09 §2

    criar_usuario(
        sessao,
        nome="Neuza Andrade",
        email="neuza@solevolt.com.br",
        senha="senha-de-teste-12",
        perfil="gerente",
    )
    cliente.post(
        "/api/entrar", json={"email": "neuza@solevolt.com.br", "senha": "senha-de-teste-12"}
    )
    cliente.get(f"/api/leads/{lead.id}/telefone")

    # S-12 §7 — cadastrar quem recebe aviso é escrita de PII, e entra na varredura.
    criar_usuario(
        sessao,
        nome="Raí Sol",
        email="rai@solevolt.com.br",
        senha="senha-de-teste-12",
        perfil="dono",
    )
    cliente.post("/api/entrar", json={"email": "rai@solevolt.com.br", "senha": "senha-de-teste-12"})
    neuza = sessao.scalars(select(Usuario).where(Usuario.perfil == "gerente")).one()
    cliente.put(
        "/api/configuracoes/telefones", json={"usuarios": {str(neuza.id): TELEFONE}}
    )

    assert _vazamentos(caplog.text) == [], "PII em claro no log da aplicação"

    trilha = [
        json.dumps({"nome": linha.nome, "dados": linha.dados}, ensure_ascii=False, default=str)
        for linha in sessao.scalars(select(Trilha)).all()
    ] + [
        json.dumps({"tipo": i.tipo, "dados": i.dados}, ensure_ascii=False, default=str)
        for i in sessao.scalars(select(Incidente)).all()
    ]
    assert trilha, "sem trilha não há varredura: o atendimento não chegou a instrumentar nada"
    assert _vazamentos("\n".join(trilha)) == [], "PII em claro na trilha"

    # ponytail: o atendimento para no turno. Estender até o Espelho e a reserva quando a
    # notificação da S-06 existir — é ela que leva PII para fora do processo.
