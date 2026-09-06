"""S-08 §4 e §5 — as duas telas do Raí.

> "Se der ruim eu quero abrir a conversa e ler. Do começo ao fim. Não quero explicação de
> engenheiro, quero ler o que foi dito."

O teste central deste arquivo é o do jargão: a mesma origem que eu leio como trace tem de
chegar nele como português. Se "span" ou "trace_id" aparecerem na tela, a tela falhou no
que ela existe para fazer.
"""

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.autenticacao import criar_usuario
from app.core.pii import cifrar, hash_telefone
from app.db import agora
from app.modelos import Conversa, Lead, Mensagem, Usuario
from app.observabilidade import registrar

SENHA = "senha-de-teste-12"
TELEFONE = "+5583988714471"
JARGAO = ("span", "trace_id", "traceid", "token", "latência", "latencia", "micro_reais")


@pytest.fixture
def rai(sessao: Session) -> Usuario:
    return criar_usuario(sessao, nome="Raí Sol", email="rai@solevolt.com.br",
                         senha=SENHA, perfil="dono")  # fmt: skip


@pytest.fixture
def atendimento(sessao: Session) -> uuid.UUID:
    """Uma conversa completa: falas, uma consulta ao estoque e uma aprovação."""
    lead = Lead(
        nome_cifrado=cifrar("Tarcísio Nóbrega"),
        telefone_cifrado=cifrar(TELEFONE),
        telefone_hash=hash_telefone(TELEFONE),
    )
    sessao.add(lead)
    sessao.flush()
    # Instante fixo nos dois lados: com `agora()` chamado duas vezes a duração dá 8
    # minutos e 59 segundos, e o teste passa a depender do relógio.
    comeco = agora()
    conversa = Conversa(
        lead_id=lead.id,
        etapa="reserva",
        criada_em=comeco,
        token_sessao=uuid.uuid4().hex,
        token_expira_em=comeco + timedelta(days=1),
        ultima_mensagem_em=comeco + timedelta(minutes=9),
    )
    sessao.add(conversa)
    sessao.flush()

    sessao.add_all(
        [
            Mensagem(
                conversa_id=conversa.id, direcao="entrada", autor="cliente", canal="web",
                conteudo="tenho uns 150 mil, queria um elétrico pra cidade",
                processada_em=agora(),
            ),  # fmt: skip
            Mensagem(
                conversa_id=conversa.id, direcao="saida", autor="aurora", canal="web",
                conteudo="Oi, Tarcísio! Me conta como você usa o carro.",
                gerada_por_ia=True, processada_em=agora(),
            ),  # fmt: skip
        ]
    )
    sessao.commit()

    registrar(
        sessao, conversa.id, "tool", "buscar_unidades",
        dados={"argumentos": {}, "retorno": [{"chassi": "9BWZZZ377VT004471"}]},
        duracao_ms=180,
    )  # fmt: skip
    registrar(
        sessao, conversa.id, "evento", "aprovacao_solicitada",
        dados={"chassi": "9BWZZZ377VT004471", "preco_centavos": 24999000},
    )  # fmt: skip
    registrar(
        sessao, conversa.id, "evento", "aprovado",
        dados={"por": "Neuza Andrade", "espelho": "SV-2026-0001", "preco_centavos": 24999000},
    )  # fmt: skip
    registrar(
        sessao, conversa.id, "turno", "aurora",
        dados={"etapa": "recomendacao", "modelo": "gpt-oss:120b", "versao_do_prompt": "aurora_v1"},
        duracao_ms=2100, custo_micro_reais=450_000,  # R$ 0,45 — o alvo por conversa da spec
    )  # fmt: skip
    return conversa.id


def _entrar(cliente: TestClient, usuario: Usuario) -> None:
    resposta = cliente.post("/api/entrar", json={"email": usuario.email, "senha": SENHA})
    assert resposta.status_code == 200


def test_o_rai_le_a_conversa_sem_jargao(
    cliente: TestClient, rai: Usuario, atendimento: uuid.UUID
) -> None:
    """S-08 §4 — o critério que esta tela existe para cumprir."""
    _entrar(cliente, rai)

    corpo = cliente.get(f"/api/atendimentos/{atendimento}").text

    for palavra in JARGAO:
        assert palavra not in corpo.lower(), f"a tela do Raí não fala de {palavra}"


def test_a_conversa_vem_em_ordem_com_as_linhas_de_acao(
    cliente: TestClient, rai: Usuario, atendimento: uuid.UUID
) -> None:
    _entrar(cliente, rai)

    dados = cliente.get(f"/api/atendimentos/{atendimento}").json()
    linhas = dados["linhas"]

    assert [linha["quando"] for linha in linhas] == sorted(
        linha["quando"] for linha in linhas
    ), "as linhas saem em ordem cronológica"
    falas = [linha for linha in linhas if linha["tipo"] == "fala"]
    acoes = [linha for linha in linhas if linha["tipo"] == "acao"]
    assert len(falas) == 2 and len(acoes) == 3

    textos = " | ".join(str(linha["texto"]) for linha in acoes)
    # ⚙ mostra onde o número veio de fora do modelo…
    assert "consultou o estoque" in textos
    # …e ⏸ mostra onde uma pessoa decidiu.
    assert "pediu aprovação" in textos
    assert "R$ 249.990,00" in textos


def test_a_tela_mostra_quem_aprovou_e_a_que_horas(
    cliente: TestClient, rai: Usuario, atendimento: uuid.UUID
) -> None:
    _entrar(cliente, rai)

    linhas = cliente.get(f"/api/atendimentos/{atendimento}").json()["linhas"]
    aprovacao = next(
        linha for linha in linhas if "APROVADO" in str(linha.get("texto", ""))
    )

    assert "Neuza Andrade" in aprovacao["texto"]
    assert aprovacao["simbolo"] == "⏸"
    assert len(aprovacao["hora"]) >= 5


def test_o_rodape_traz_custo_duracao_e_a_versao_do_prompt(
    cliente: TestClient, rai: Usuario, atendimento: uuid.UUID
) -> None:
    _entrar(cliente, rai)

    rodape = cliente.get(f"/api/atendimentos/{atendimento}").json()["rodape"]

    assert rodape["custo"] == "R$ 0,45"
    assert rodape["turnos"] == 1
    assert rodape["modelo"] == "gpt-oss:120b"
    assert rodape["versao_do_prompt"] == "aurora_v1"
    assert rodape["duracao_min"] == 9


def test_a_lista_mostra_o_nome_mascarado_e_nunca_o_telefone(
    cliente: TestClient, rai: Usuario, atendimento: uuid.UUID
) -> None:
    _entrar(cliente, rai)

    corpo = cliente.get("/api/atendimentos").text

    assert "Tarcísio N." in corpo
    assert "Nóbrega" not in corpo
    assert "988714471" not in corpo


def test_busca_por_telefone_usa_o_hash_e_acha_o_cliente_que_volta(
    cliente: TestClient, rai: Usuario, atendimento: uuid.UUID
) -> None:
    """ADR-007 — reconhecer o cliente que volta sem decifrar nada é a razão do hash."""
    _entrar(cliente, rai)

    achados = cliente.get("/api/atendimentos", params={"busca": "(83) 98871-4471"}).json()

    assert len(achados) == 1
    assert achados[0]["id"] == str(atendimento)


def test_busca_por_telefone_de_outro_nao_traz_nada(
    cliente: TestClient, rai: Usuario, atendimento: uuid.UUID
) -> None:
    _entrar(cliente, rai)
    assert cliente.get("/api/atendimentos", params={"busca": "(83) 99999-0000"}).json() == []


def test_busca_por_nome_encontra(
    cliente: TestClient, rai: Usuario, atendimento: uuid.UUID
) -> None:
    _entrar(cliente, rai)
    assert len(cliente.get("/api/atendimentos", params={"busca": "Tarcísio"}).json()) == 1


def test_vendedor_nao_alcanca_os_atendimentos(cliente: TestClient, sessao: Session) -> None:
    from app.modelos import Vendedor

    vendedor = Vendedor(nome="Jaqueline Alves", telefone_cifrado=cifrar("+5583988880002"))
    sessao.add(vendedor)
    sessao.flush()
    usuario = criar_usuario(sessao, nome="Jaqueline Alves", email="j@solevolt.com.br",
                            senha=SENHA, perfil="vendedor", vendedor_id=vendedor.id)  # fmt: skip
    _entrar(cliente, usuario)

    assert cliente.get("/api/atendimentos").status_code == 404
    assert cliente.get("/api/custo").status_code == 404


# ── §5 · o painel de custo ───────────────────────────────────────────────────────


def test_o_painel_responde_a_fala_do_rai(
    cliente: TestClient, rai: Usuario, atendimento: uuid.UUID
) -> None:
    """Gasto, teto e a distância entre os dois. Sem tabela de tokens."""
    _entrar(cliente, rai)

    dados = cliente.get("/api/custo").json()

    assert dados["gasto"] == "R$ 0,45"
    assert dados["teto"] == "R$ 900,00"
    assert dados["conversas"] == 1
    assert dados["faixa"] == "verde"
    assert len(dados["acumulado_por_dia"]) == 1
    for palavra in JARGAO:
        assert palavra not in cliente.get("/api/custo").text.lower()


def test_a_faixa_muda_de_cor_conforme_o_teto(
    cliente: TestClient, sessao: Session, rai: Usuario
) -> None:
    from app.observabilidade import TETO_MICRO_REAIS

    registrar(sessao, None, "turno", "aurora", custo_micro_reais=int(TETO_MICRO_REAIS * 0.85))
    _entrar(cliente, rai)

    assert cliente.get("/api/custo").json()["faixa"] == "vermelho"


def test_o_painel_avisa_quando_o_custo_nao_e_faturado(
    cliente: TestClient, sessao: Session, rai: Usuario, atendimento: uuid.UUID
) -> None:
    """ADR-012 — R$ 0,00 com cara de verdade é o teto parando de proteger em silêncio."""
    registrar(
        sessao, atendimento, "turno", "aurora", custo_micro_reais=0, custo_faturado=False
    )
    _entrar(cliente, rai)

    assert cliente.get("/api/custo").json()["conversas_sem_custo_faturado"] == 1


def test_as_paginas_do_rai_sobem(cliente: TestClient) -> None:
    for caminho in ("/atendimentos", "/custo"):
        resposta = cliente.get(caminho)
        assert resposta.status_code == 200
        assert "text/html" in resposta.headers["content-type"]


def test_um_cadastro_ilegivel_nao_derruba_a_tela(
    cliente: TestClient, sessao: Session, rai: Usuario, atendimento: uuid.UUID
) -> None:
    """Regressão de um caso real: a lista voltava 500 por causa de UM lead antigo.

    Chave girada, blob corrompido — acontece. O cliente ilegível aparece como `?` e os
    outros continuam visíveis, que é a mesma regra que o `Lead.__repr__` já seguia.
    """
    quebrado = Lead(
        nome_cifrado=b"isto-nao-decifra",
        telefone_cifrado=cifrar("+5583999990000"),
        telefone_hash=hash_telefone("+5583999990000"),
    )
    sessao.add(quebrado)
    sessao.flush()
    sessao.add(
        Conversa(
            lead_id=quebrado.id,
            token_sessao=uuid.uuid4().hex,
            token_expira_em=agora() + timedelta(days=1),
        )
    )
    sessao.commit()
    _entrar(cliente, rai)

    resposta = cliente.get("/api/atendimentos")

    assert resposta.status_code == 200
    nomes = [linha["cliente"] for linha in resposta.json()]
    assert "?" in nomes, "o ilegível aparece como ?"
    assert "Tarcísio N." in nomes, "e os outros continuam visíveis"
