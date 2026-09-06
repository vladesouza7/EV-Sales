"""S-11 — critérios de aceite da autenticação.

O que se testa aqui é o que a assinatura do JWT **não** garante sozinha: que o token não
carrega PII, que desativar alguém vale na hora, que renovar não reinicia o relógio das 12
horas, e que o recorte do vendedor é `WHERE` e não filtro de tela.
"""

import uuid
from datetime import timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.autenticacao import (
    LIMITE_ABSOLUTO,
    TENTATIVAS_MAXIMAS,
    VALIDADE,
    criar_usuario,
    emitir,
)
from app.db import agora
from app.modelos import Conversa, Lead, Trilha, Usuario, Vendedor

SENHA = "senha-de-teste-12"


@pytest.fixture
def neuza(sessao: Session) -> Usuario:
    return criar_usuario(sessao, nome="Neuza Andrade", email="neuza@solevolt.com.br",
                         senha=SENHA, perfil="gerente")  # fmt: skip


@pytest.fixture
def rai(sessao: Session) -> Usuario:
    return criar_usuario(sessao, nome="Raí Sol", email="rai@solevolt.com.br",
                         senha=SENHA, perfil="dono")  # fmt: skip


def _entrar(cliente: TestClient, email: str, senha: str = SENHA) -> int:
    return cliente.post("/api/entrar", json={"email": email, "senha": senha}).status_code


def test_entra_e_a_sessao_vale(cliente: TestClient, neuza: Usuario) -> None:
    assert _entrar(cliente, neuza.email) == 200
    eu = cliente.get("/api/eu").json()
    assert eu == {"nome": "Neuza Andrade", "perfil": "gerente"}


def test_email_inexistente_e_senha_errada_respondem_igual(
    cliente: TestClient, neuza: Usuario
) -> None:
    inexistente = cliente.post(
        "/api/entrar", json={"email": "ninguem@solevolt.com.br", "senha": SENHA}
    )
    errada = cliente.post("/api/entrar", json={"email": neuza.email, "senha": "outra-coisa-12"})

    assert inexistente.status_code == errada.status_code == 401
    assert inexistente.json() == errada.json()


def test_conta_bloqueia_depois_de_cinco_tentativas(
    cliente: TestClient, sessao: Session, neuza: Usuario
) -> None:
    for _ in range(TENTATIVAS_MAXIMAS):
        assert _entrar(cliente, neuza.email, "errada-de-proposito") == 401

    # A sexta é recusada mesmo com a senha certa, e com a mesma mensagem.
    assert _entrar(cliente, neuza.email, SENHA) == 401
    sessao.expire_all()
    assert sessao.get(Usuario, neuza.id).bloqueado_ate is not None  # type: ignore[union-attr]


def test_o_token_nao_carrega_pii(cliente: TestClient, neuza: Usuario) -> None:
    _entrar(cliente, neuza.email)
    payload = jwt.decode(
        cliente.cookies["ev_staff"], options={"verify_signature": False}, algorithms=["HS256"]
    )

    assert set(payload) == {"sub", "perfil", "iat", "exp", "inicio"}
    despejo = str(payload)
    assert "Neuza" not in despejo and "solevolt" not in despejo


def test_assinatura_adulterada_nao_entra(cliente: TestClient, neuza: Usuario) -> None:
    _entrar(cliente, neuza.email)
    bom = cliente.cookies["ev_staff"]
    cabecalho, corpo, assinatura = bom.split(".")
    falsificado = jwt.encode({"sub": str(neuza.id), "perfil": "dono"}, "o" * 32)
    adulterado = f"{falsificado.rsplit('.', 1)[0]}.{assinatura}"

    cliente.cookies.set("ev_staff", adulterado)
    assert cliente.get("/api/eu").status_code == 401


def test_usuario_desativado_sai_na_hora(
    cliente: TestClient, sessao: Session, neuza: Usuario
) -> None:
    _entrar(cliente, neuza.email)
    assert cliente.get("/api/eu").status_code == 200

    alvo = sessao.get(Usuario, neuza.id)
    assert alvo is not None
    alvo.ativo = False
    sessao.commit()

    assert cliente.get("/api/eu").status_code == 401


def test_revogar_derruba_todos_os_tokens_daquela_pessoa(
    cliente: TestClient, sessao: Session, neuza: Usuario
) -> None:
    _entrar(cliente, neuza.email)
    alvo = sessao.get(Usuario, neuza.id)
    assert alvo is not None
    alvo.sessoes_validas_apos = agora() + timedelta(seconds=1)
    sessao.commit()

    assert cliente.get("/api/eu").status_code == 401
    # Entrar de novo volta a funcionar: o carimbo derruba o passado, não a conta.
    assert _entrar(cliente, neuza.email) == 200


def test_token_expirado_e_recusado(cliente: TestClient, neuza: Usuario) -> None:
    vencido = emitir(neuza, emitido_em=agora() - VALIDADE - timedelta(minutes=1))
    cliente.cookies.set("ev_staff", vencido)

    assert cliente.get("/api/eu").status_code == 401


def test_renovar_nao_reinicia_o_relogio_das_doze_horas(
    cliente: TestClient, neuza: Usuario
) -> None:
    """A claim `inicio` é copiada adiante — senão renovar seria sessão eterna."""
    antigo = agora() - LIMITE_ABSOLUTO - timedelta(minutes=1)
    cliente.cookies.set("ev_staff", emitir(neuza, inicio=antigo))

    assert cliente.get("/api/eu").status_code == 401


def test_perto_do_fim_a_resposta_traz_token_novo(cliente: TestClient, neuza: Usuario) -> None:
    inicio = agora() - timedelta(hours=1)
    quase = agora() - VALIDADE + timedelta(minutes=5)  # faltam 5 min para o exp
    cliente.cookies.set("ev_staff", emitir(neuza, emitido_em=quase, inicio=inicio))

    resposta = cliente.get("/api/eu")

    assert resposta.status_code == 200
    # Lido do cabeçalho, e não do jar: injetar um cookie à mão e receber outro do servidor
    # deixa dois com o mesmo nome no cliente de teste, o que é ruído do teste e não do app.
    enviado = resposta.headers["set-cookie"].split("ev_staff=")[1].split(";")[0]
    novo = jwt.decode(enviado, options={"verify_signature": False}, algorithms=["HS256"])
    assert novo["inicio"] == int(inicio.timestamp()), "o início original é preservado"
    assert novo["exp"] > int((agora() + VALIDADE - timedelta(minutes=1)).timestamp())


def test_sair_limpa_o_cookie(cliente: TestClient, neuza: Usuario) -> None:
    _entrar(cliente, neuza.email)
    assert cliente.post("/api/sair").status_code == 200
    assert cliente.get("/api/eu").status_code == 401


def test_trocar_a_senha_derruba_as_proprias_sessoes(cliente: TestClient, neuza: Usuario) -> None:
    _entrar(cliente, neuza.email)
    trocada = cliente.post(
        "/api/minha-senha", json={"atual": SENHA, "nova": "outra-senha-bem-longa"}
    )

    assert trocada.status_code == 200
    assert cliente.get("/api/eu").status_code == 401
    assert _entrar(cliente, neuza.email, "outra-senha-bem-longa") == 200


def test_senha_curta_e_recusada(cliente: TestClient, neuza: Usuario) -> None:
    _entrar(cliente, neuza.email)
    curta = cliente.post("/api/minha-senha", json={"atual": SENHA, "nova": "curta"})
    assert curta.status_code == 400


def test_so_o_dono_alcanca_a_tela_de_usuarios(
    cliente: TestClient, neuza: Usuario, rai: Usuario
) -> None:
    _entrar(cliente, neuza.email)
    # 404 e não 403: responder "existe, mas não é seu" já é dizer que existe.
    assert cliente.get("/api/usuarios").status_code == 404

    cliente.post("/api/sair")
    _entrar(cliente, rai.email)
    assert cliente.get("/api/usuarios").status_code == 200


def test_sem_sessao_nao_alcanca_nada(cliente: TestClient) -> None:
    assert cliente.get("/api/eu").status_code == 401
    assert cliente.get("/api/usuarios").status_code == 401


# ── §6 — ver telefone completo deixa rastro ──────────────────────────────────────


@pytest.fixture
def lead_do_tarcisio(sessao: Session) -> tuple[Lead, Usuario, Usuario]:
    from app.core.pii import cifrar, hash_telefone

    tarcisio = Vendedor(nome="Tarcísio Lima", telefone_cifrado=cifrar("+5583988880001"))
    jaqueline = Vendedor(nome="Jaqueline Alves", telefone_cifrado=cifrar("+5583988880002"))
    sessao.add_all([tarcisio, jaqueline])
    sessao.flush()

    lead = Lead(
        nome_cifrado=cifrar("Almir Nóbrega"),
        telefone_cifrado=cifrar("+5583988714471"),
        telefone_hash=hash_telefone("+5583988714471"),
    )
    sessao.add(lead)
    sessao.flush()
    sessao.add(
        Conversa(
            lead_id=lead.id,
            atendente_id=tarcisio.id,
            token_sessao=uuid.uuid4().hex,
            token_expira_em=agora() + timedelta(days=1),
        )
    )
    usuario_t = criar_usuario(sessao, nome="Tarcísio Lima", email="tarcisio@solevolt.com.br",
                              senha=SENHA, perfil="vendedor", vendedor_id=tarcisio.id)  # fmt: skip
    usuario_j = criar_usuario(sessao, nome="Jaqueline Alves", email="jaqueline@solevolt.com.br",
                              senha=SENHA, perfil="vendedor", vendedor_id=jaqueline.id)  # fmt: skip
    return lead, usuario_t, usuario_j


def test_ver_telefone_completo_deixa_rastro_sem_o_telefone(
    cliente: TestClient, sessao: Session, lead_do_tarcisio: tuple[Lead, Usuario, Usuario]
) -> None:
    lead, tarcisio, _ = lead_do_tarcisio
    _entrar(cliente, tarcisio.email)

    resposta = cliente.get(f"/api/leads/{lead.id}/telefone")

    assert resposta.status_code == 200
    assert resposta.json()["telefone"] == "+5583988714471"
    linha = sessao.scalars(select(Trilha).where(Trilha.nome == "telefone_visto")).one()
    assert linha.dados["usuario_id"] == str(tarcisio.id)
    assert linha.dados["lead_id"] == str(lead.id)
    # O que se audita é o acesso. Guardar o número aqui seria uma segunda cópia da PII.
    assert "988714471" not in str(linha.dados)


def test_vendedor_nao_ve_o_lead_do_outro_vendedor(
    cliente: TestClient, lead_do_tarcisio: tuple[Lead, Usuario, Usuario]
) -> None:
    lead, _, jaqueline = lead_do_tarcisio
    _entrar(cliente, jaqueline.email)

    assert cliente.get(f"/api/leads/{lead.id}/telefone").status_code == 404


def test_gerente_ve_o_telefone_de_qualquer_lead(
    cliente: TestClient, neuza: Usuario, lead_do_tarcisio: tuple[Lead, Usuario, Usuario]
) -> None:
    lead, _, _ = lead_do_tarcisio
    _entrar(cliente, neuza.email)

    assert cliente.get(f"/api/leads/{lead.id}/telefone").status_code == 200
