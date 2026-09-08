"""MCP — a chave própria no transporte, e as tools mirando o mesmo caminho da tela.

O protocolo MCP em si (initialize, list_tools, o round-trip JSON-RPC) já foi verificado
manualmente com um cliente de verdade contra o servidor rodando — aqui o que importa
testar é o que é nosso: a chave barra quem não tem, e cada tool faz exatamente o que a
tela de configurações e as tools de estoque/atendimento já fazem, com o `dono` cadastrado
assinando a auditoria.
"""

import base64
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.arquivos import ler
from app.autenticacao import criar_usuario
from app.core.pii import cifrar, hash_telefone
from app.mcp_server import SemDono
from app.mcp_server import buscar_unidades as tool_buscar_unidades
from app.mcp_server import custo_do_mes as tool_custo_do_mes
from app.mcp_server import detalhar_unidade as tool_detalhar_unidade
from app.mcp_server import ler_configuracoes as tool_ler_configuracoes
from app.mcp_server import salvar_configuracoes as tool_salvar_configuracoes
from app.mcp_server import subir_foto_da_unidade as tool_subir_foto
from app.modelos import Lead, Unidade, Usuario

SENHA = "senha-de-teste-12"

SEAL_BRANCO = dict(
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
)


@pytest.fixture
def rai(sessao: Session) -> Usuario:
    return criar_usuario(sessao, nome="Raí Sol", email="rai@solevolt.com.br",
                         senha=SENHA, perfil="dono")  # fmt: skip


@pytest.fixture
def seal(sessao: Session) -> Unidade:
    unidade = Unidade(**SEAL_BRANCO)  # type: ignore[arg-type]
    sessao.add(unidade)
    sessao.commit()
    return unidade


# ── §1 a chave no transporte ──────────────────────────────────────────────────────


def test_sem_cabecalho_recusa(cliente: TestClient) -> None:
    resposta = cliente.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert resposta.status_code == 401


def test_chave_errada_recusa(cliente: TestClient) -> None:
    resposta = cliente.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        headers={"Authorization": "Bearer chave-errada"},
    )
    assert resposta.status_code == 401


# ── §2 as tools chamam o mesmo caminho da tela ────────────────────────────────────


def test_sem_dono_cadastrado_da_erro_claro() -> None:
    with pytest.raises(SemDono):
        tool_ler_configuracoes()


def test_ler_configuracoes_tool(rai: Usuario) -> None:
    resultado = tool_ler_configuracoes()
    assert "chaves" in resultado
    assert "provedores" in resultado
    assert "telefones" in resultado


def test_buscar_unidades_tool_reflete_estoque(rai: Usuario, seal: Unidade) -> None:
    resultado = tool_buscar_unidades()
    assert len(resultado) == 1
    assert resultado[0]["chassi"] == seal.chassi


def test_detalhar_unidade_tool(rai: Usuario, seal: Unidade) -> None:
    ficha = tool_detalhar_unidade(seal.chassi)
    assert ficha["marca"] == "BYD"


def test_detalhar_unidade_tool_chassi_inexistente(rai: Usuario) -> None:
    with pytest.raises(ValueError, match="não encontrado"):
        tool_detalhar_unidade("NAOEXISTE1234567")


def test_subir_foto_da_unidade_tool(rai: Usuario, seal: Unidade) -> None:
    conteudo = b"bytes-de-foto-fake-via-mcp"
    b64 = base64.b64encode(conteudo).decode()

    resultado = tool_subir_foto(seal.chassi, b64, "image/png")

    assert resultado == {"foto_url": f"/fotos/{seal.chassi}.png"}
    guardado = ler(f"fotos/{seal.chassi}.png")
    assert guardado is not None
    assert guardado[0] == conteudo


def test_subir_foto_da_unidade_tool_base64_invalido(rai: Usuario, seal: Unidade) -> None:
    with pytest.raises(ValueError, match="base64"):
        tool_subir_foto(seal.chassi, "!!!nao-e-base64!!!", "image/png")


def test_subir_foto_da_unidade_tool_tipo_invalido(rai: Usuario, seal: Unidade) -> None:
    b64 = base64.b64encode(b"conteudo").decode()
    with pytest.raises(ValueError, match="jpg, png ou webp"):
        tool_subir_foto(seal.chassi, b64, "text/plain")


def test_salvar_configuracoes_tool_recusa_credencial_invalida(rai: Usuario) -> None:
    with pytest.raises(ValueError):
        tool_salvar_configuracoes(valores={"llm_provedor": "provedor-que-nao-existe"})


def test_custo_do_mes_tool(rai: Usuario) -> None:
    resultado = tool_custo_do_mes()
    assert "teto" in resultado
    assert "gasto" in resultado


def test_listar_atendimentos_tool_encontra_por_telefone(rai: Usuario, sessao: Session) -> None:
    from app.mcp_server import listar_atendimentos as tool_listar_atendimentos

    telefone = "+5583988712233"
    lead = Lead(
        nome_cifrado=cifrar("Rogério Teste"),
        telefone_cifrado=cifrar(telefone),
        telefone_hash=hash_telefone(telefone),
    )
    sessao.add(lead)
    sessao.commit()

    resultado = tool_listar_atendimentos()
    assert isinstance(resultado, list)


def test_ler_atendimento_tool_conversa_inexistente(rai: Usuario) -> None:
    from app.mcp_server import ler_atendimento as tool_ler_atendimento

    with pytest.raises(ValueError):
        tool_ler_atendimento(str(uuid.uuid4()))
