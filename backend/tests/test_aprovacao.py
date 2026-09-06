"""S-04 — critérios de aceite da fila de aprovação e do Espelho.

O primeiro teste é o mais importante do arquivo: ele tenta inserir um espelho sem
aprovação e exige que o **banco** recuse. É a invariante 3, e ela precisa valer mesmo
quando o código de aplicação estiver errado.
"""

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.aprovacao import VALIDADE_DO_PEDIDO, solicitar_aprovacao
from app.autenticacao import criar_usuario
from app.core.pii import cifrar, hash_telefone
from app.db import agora
from app.ia.etapas import tools_da_etapa
from app.ia.registro import disponiveis
from app.modelos import Conversa, Espelho, Lead, PedidoDeAprovacao, Unidade, Usuario

SENHA = "senha-de-teste-12"
BARRA = chr(92)
SEAL = dict(
    chassi="9BWZZZ377VT004471", marca="BYD", modelo="Seal", versao="Design", ano=2026,
    cor="Branco", condicao="novo", km=0, preco_centavos=24999000,
    autonomia_km=372, autonomia_fonte="INMETRO_PBEV_2026", status="disponivel",
)  # fmt: skip


@pytest.fixture
def neuza(sessao: Session) -> Usuario:
    return criar_usuario(sessao, nome="Neuza Andrade", email="neuza@solevolt.com.br",
                         senha=SENHA, perfil="gerente")  # fmt: skip


@pytest.fixture
def conversa(sessao: Session) -> Conversa:
    sessao.add(Unidade(**SEAL))  # type: ignore[arg-type]
    lead = Lead(
        nome_cifrado=cifrar("Tarcísio Nóbrega"),
        telefone_cifrado=cifrar("+5583988714471"),
        telefone_hash=hash_telefone("+5583988714471"),
    )
    sessao.add(lead)
    sessao.flush()
    conversa = Conversa(
        lead_id=lead.id,
        etapa="condicao",
        qualificacao={"uso": "cidade", "km_dia": 40},
        token_sessao=uuid.uuid4().hex,
        token_expira_em=agora() + timedelta(days=1),
    )
    sessao.add(conversa)
    sessao.commit()
    return conversa


def _entrar(cliente: TestClient, usuario: Usuario) -> None:
    assert cliente.post(
        "/api/entrar", json={"email": usuario.email, "senha": SENHA}
    ).status_code == 200


# ── a invariante 3 ───────────────────────────────────────────────────────────────


def test_o_banco_recusa_espelho_sem_aprovacao(sessao: Session, conversa: Conversa) -> None:
    """Invariante 3 — mesmo que o código de aplicação esteja errado, o banco recusa."""
    with pytest.raises(IntegrityError):
        sessao.execute(
            text(
                "INSERT INTO espelhos (id, conversa_id, chassi, approval_id, preco_centavos,"
                " numero, pdf_objeto, valido_ate, emitido_em)"
                " VALUES (gen_random_uuid(), :c, :ch, NULL, 1, 'SV-X', 'x', now(), now())"
            ),
            {"c": conversa.id, "ch": SEAL["chassi"]},
        )
    sessao.rollback()
    assert sessao.scalars(select(Espelho)).all() == []


# ── a pausa ──────────────────────────────────────────────────────────────────────


def test_a_aurora_para_e_nao_emite_sozinha(sessao: Session, conversa: Conversa) -> None:
    resultado = solicitar_aprovacao(sessao, conversa, str(SEAL["chassi"]))

    assert resultado["resultado"] == "aguardando_aprovacao"
    assert conversa.etapa == "aguardando_aprovacao"
    assert tools_da_etapa("aguardando_aprovacao") == ()
    assert disponiveis("aguardando_aprovacao") == ()
    assert sessao.scalars(select(Espelho)).all() == []


def test_o_preco_vem_do_banco_e_nao_do_agente(sessao: Session, conversa: Conversa) -> None:
    """O agente pode ter escrito R$ 240.000 no texto. O pedido lê o Postgres."""
    solicitar_aprovacao(sessao, conversa, str(SEAL["chassi"]))

    pedido = sessao.scalars(select(PedidoDeAprovacao)).one()
    assert pedido.preco_centavos == 24999000


def test_unidade_indisponivel_nao_para_a_conversa(sessao: Session, conversa: Conversa) -> None:
    unidade = sessao.get(Unidade, SEAL["chassi"])
    assert unidade is not None
    unidade.status = "vendido"
    sessao.commit()

    resultado = solicitar_aprovacao(sessao, conversa, str(SEAL["chassi"]))

    assert resultado["resultado"] == "indisponivel"
    assert conversa.etapa == "condicao"


def test_uma_conversa_nao_tem_dois_pedidos_pendentes(
    sessao: Session, conversa: Conversa
) -> None:
    """O índice parcial é a garantia; a etapa sem tools é o primeiro portão."""
    solicitar_aprovacao(sessao, conversa, str(SEAL["chassi"]))
    conversa.etapa = "condicao"  # simula o portão da etapa tendo falhado
    sessao.commit()

    with pytest.raises(IntegrityError):
        solicitar_aprovacao(sessao, conversa, str(SEAL["chassi"]))
    sessao.rollback()


# ── a decisão ────────────────────────────────────────────────────────────────────


def test_aprovar_emite_o_espelho_e_manda_a_conversa_para_reserva(
    cliente: TestClient, sessao: Session, conversa: Conversa, neuza: Usuario
) -> None:
    solicitar_aprovacao(sessao, conversa, str(SEAL["chassi"]))
    pedido = sessao.scalars(select(PedidoDeAprovacao)).one()
    _entrar(cliente, neuza)

    resposta = cliente.post(f"/api/aprovacoes/{pedido.id}/aprovar")

    assert resposta.status_code == 200
    assert resposta.json()["espelho"].startswith("SV-")
    sessao.expire_all()
    espelho = sessao.scalars(select(Espelho)).one()
    assert espelho.approval_id == pedido.id
    assert espelho.preco_centavos == 24999000
    assert sessao.get(PedidoDeAprovacao, pedido.id).decidido_por == neuza.id  # type: ignore[union-attr]
    assert sessao.get(Conversa, conversa.id).etapa == "reserva"  # type: ignore[union-attr]


def test_preco_alterado_na_espera_invalida_a_aprovacao(
    cliente: TestClient, sessao: Session, conversa: Conversa, neuza: Usuario
) -> None:
    solicitar_aprovacao(sessao, conversa, str(SEAL["chassi"]))
    pedido = sessao.scalars(select(PedidoDeAprovacao)).one()

    unidade = sessao.get(Unidade, SEAL["chassi"])
    assert unidade is not None
    unidade.preco_centavos = 25499000
    sessao.commit()

    _entrar(cliente, neuza)
    resposta = cliente.post(f"/api/aprovacoes/{pedido.id}/aprovar")

    assert resposta.status_code == 409
    sessao.expire_all()
    assert sessao.get(PedidoDeAprovacao, pedido.id).status == "expirado"  # type: ignore[union-attr]
    assert sessao.scalars(select(Espelho)).all() == []
    novo = sessao.scalars(
        select(PedidoDeAprovacao).where(PedidoDeAprovacao.status == "pendente")
    ).one()
    assert novo.preco_centavos == 25499000


def test_pedido_vencido_sai_da_fila(
    cliente: TestClient, sessao: Session, conversa: Conversa, neuza: Usuario
) -> None:
    solicitar_aprovacao(sessao, conversa, str(SEAL["chassi"]))
    pedido = sessao.scalars(select(PedidoDeAprovacao)).one()
    pedido.criado_em = agora() - VALIDADE_DO_PEDIDO - timedelta(minutes=1)
    pedido.expira_em = agora() - timedelta(minutes=1)
    sessao.commit()

    _entrar(cliente, neuza)
    assert cliente.get("/api/aprovacoes").json() == []
    assert cliente.post(f"/api/aprovacoes/{pedido.id}/aprovar").status_code == 409
    sessao.expire_all()
    assert sessao.get(PedidoDeAprovacao, pedido.id).status == "expirado"  # type: ignore[union-attr]


def test_escalonamento_para_o_rai_depois_de_quinze_minutos(
    cliente: TestClient, sessao: Session, conversa: Conversa, neuza: Usuario
) -> None:
    solicitar_aprovacao(sessao, conversa, str(SEAL["chassi"]))
    pedido = sessao.scalars(select(PedidoDeAprovacao)).one()
    pedido.criado_em = agora() - timedelta(minutes=16)
    sessao.commit()

    _entrar(cliente, neuza)
    card = cliente.get("/api/aprovacoes").json()[0]

    assert card["escalado"] is True
    sessao.expire_all()
    assert sessao.get(PedidoDeAprovacao, pedido.id).escalado_em is not None  # type: ignore[union-attr]


def test_recusa_manda_para_humano_sem_contar_o_motivo_ao_cliente(
    cliente: TestClient, sessao: Session, conversa: Conversa, neuza: Usuario
) -> None:
    solicitar_aprovacao(sessao, conversa, str(SEAL["chassi"]))
    pedido = sessao.scalars(select(PedidoDeAprovacao)).one()
    _entrar(cliente, neuza)

    resposta = cliente.post(
        f"/api/aprovacoes/{pedido.id}/recusar", json={"motivo": "unidade prometida"}
    )

    assert resposta.status_code == 200
    # O motivo fica no pedido, para o vendedor. Não vai para o texto do cliente.
    assert "unidade prometida" not in resposta.json()["mensagem"]
    sessao.expire_all()
    atualizada = sessao.get(Conversa, conversa.id)
    assert atualizada is not None and atualizada.modo == "humano"
    assert sessao.get(PedidoDeAprovacao, pedido.id).motivo_rejeicao == "unidade prometida"  # type: ignore[union-attr]


def test_motivo_fora_da_lista_e_recusado(
    cliente: TestClient, sessao: Session, conversa: Conversa, neuza: Usuario
) -> None:
    solicitar_aprovacao(sessao, conversa, str(SEAL["chassi"]))
    pedido = sessao.scalars(select(PedidoDeAprovacao)).one()
    _entrar(cliente, neuza)

    recusada = cliente.post(
        f"/api/aprovacoes/{pedido.id}/recusar", json={"motivo": "não gostei do cliente"}
    )
    assert recusada.status_code == 400


def test_vendedor_nao_alcanca_a_fila(cliente: TestClient, sessao: Session) -> None:
    from app.modelos import Vendedor

    vendedor = Vendedor(nome="Tarcísio Lima", telefone_cifrado=cifrar("+5583988880001"))
    sessao.add(vendedor)
    sessao.flush()
    usuario = criar_usuario(sessao, nome="Tarcísio Lima", email="t@solevolt.com.br",
                            senha=SENHA, perfil="vendedor", vendedor_id=vendedor.id)  # fmt: skip
    _entrar(cliente, usuario)

    # 404 e não 403 — a mesma regra da S-02 para conversa de outro cliente.
    assert cliente.get("/api/aprovacoes").status_code == 404


def test_a_fila_nao_vaza_pii(
    cliente: TestClient, sessao: Session, conversa: Conversa, neuza: Usuario
) -> None:
    solicitar_aprovacao(sessao, conversa, str(SEAL["chassi"]))
    _entrar(cliente, neuza)

    corpo = cliente.get("/api/aprovacoes").text

    assert "Tarcísio N." in corpo
    assert "Nóbrega" not in corpo
    assert "988714471" not in corpo
    # O chassi inteiro também não: o card mostra os quatro últimos.
    assert SEAL["chassi"] not in corpo


# ── o documento ──────────────────────────────────────────────────────────────────


def test_o_espelho_nao_e_documento_de_venda() -> None:
    """S-04 §5 — o que o PDF NÃO contém é tão parte da spec quanto o que contém."""
    from app.espelho import gerar

    pdf = gerar(
        numero="SV-2026-0001",
        nome_do_cliente="Tarcísio Nóbrega",
        unidade={
            "chassi": SEAL["chassi"],
            "marca": "BYD",
            "modelo": "Seal",
            "versao": "Design",
            "ano": 2026,
            "cor": "Branco",
            "condicao": "novo",
            "autonomia_texto": "372 km pelo Inmetro, no PBEV 2026",
        },
        preco_centavos=24999000,
        aprovado_por="Neuza Andrade",
        emitido_em=agora(),
    )

    texto = _texto_do_pdf(pdf)
    # O proibido é o CAMPO, não a palavra: o rodapé cita financiamento justamente para
    # dizer que ele não está aqui, e é o que separa um espelho de um contrato.
    for campo in ("parcela", "taxa", "prazo de crédito", "nota fiscal", "nf-e",
                  "assinatura", "prazo de entrega", "vencimento"):  # fmt: skip
        assert campo not in texto.lower(), f"o Espelho não pode ter campo de {campo}"

    minusculo = texto.lower()
    assert minusculo.count("financiamento") == 1, "só o rodapé fala de financiamento"
    assert "o financiamento e a documentação são tratados" in minusculo

    assert "249.990,00" in texto
    assert "duzentos e quarenta e nove mil, novecentos e noventa reais" in texto
    assert "7 dias corridos" in texto
    assert "72 horas" in texto
    assert "Neuza Andrade" in texto
    assert SEAL["chassi"] in texto
    assert "presencialmente na loja" in texto


def _texto_do_pdf(pdf: bytes) -> str:
    """Extrai os literais de texto do PDF, sem trazer um leitor de PDF só para o teste.

    Não é um parser de PDF: é o suficiente para conferir o que ESTE gerador escreveu.
    Feito com varredura de bytes em vez de regex de propósito — o padrão para literais
    com parênteses escapados é exatamente o tipo de expressão que se escreve errado.
    """
    import zlib

    bruto = pdf
    marca, fim = b"stream", b"endstream"
    posicao = bruto.find(marca)
    while posicao != -1:
        final = pdf.find(fim, posicao)
        if final == -1:
            break
        try:
            bruto += zlib.decompress(pdf[posicao + len(marca) : final].strip())
        except zlib.error:
            pass
        posicao = pdf.find(marca, final)

    escape = ord(BARRA)
    literais: list[str] = []
    atual: list[str] = []
    dentro = escapando = False
    for byte in bruto:
        if escapando:
            atual.append(chr(byte))
            escapando = False
        elif byte == escape:
            escapando = True
        elif byte == ord("("):
            dentro, atual = True, []
        elif byte == ord(")") and dentro:
            literais.append("".join(atual))
            dentro = False
        elif dentro:
            atual.append(chr(byte))
    return " ".join(literais)


# ── as telas (§4) e o link de uso único (S-11 §7) ────────────────────────────────


def test_as_paginas_sobem(cliente: TestClient) -> None:
    for caminho in ("/entrar", "/aprovacoes"):
        resposta = cliente.get(caminho)
        assert resposta.status_code == 200
        assert "text/html" in resposta.headers["content-type"]


def test_o_link_de_uso_unico_leva_ao_card_e_so_serve_uma_vez(
    cliente: TestClient, sessao: Session, conversa: Conversa
) -> None:
    solicitar_aprovacao(sessao, conversa, str(SEAL["chassi"]))
    pedido = sessao.scalars(select(PedidoDeAprovacao)).one()

    primeira = cliente.get(f"/a/{pedido.codigo}", follow_redirects=False)
    assert primeira.status_code == 303
    assert primeira.headers["location"] == f"/aprovacoes?pedido={pedido.id}"

    # Segunda vez cai na fila: o código é de uso único (S-04 §3).
    segunda = cliente.get(f"/a/{pedido.codigo}", follow_redirects=False)
    assert segunda.headers["location"] == "/aprovacoes"


def test_codigo_invalido_nao_conta_se_existe(cliente: TestClient) -> None:
    """Distinguir "não existe" de "já usado" contaria a quem sonda que o código valia."""
    resposta = cliente.get("/a/naoexiste", follow_redirects=False)
    assert resposta.status_code == 303
    assert resposta.headers["location"] == "/aprovacoes"
