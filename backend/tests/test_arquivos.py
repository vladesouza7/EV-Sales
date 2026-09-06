"""ADR-013 — armazenamento de arquivo, e a linha perigosa dele.

O bucket é privado e tem dois prefixos com risco diferente: `fotos/` não tem PII e é
servido pelo próprio app; `documentos/` guarda o Espelho, que tem o nome do cliente e o
chassi reservado para ele, e só sai por URL assinada.

A rota pública `/fotos/{objeto}` é o único ponto onde um nome vindo da internet vira
caminho de objeto. Se ela aceitar `../documentos/...`, o Espelho fica atrás de uma URL
adivinhável — e nenhuma das proteções do ADR-007 alcança isso.
"""

import pytest
from fastapi.testclient import TestClient

from app.arquivos import PREFIXO_DOCUMENTOS, PREFIXO_FOTOS, NomeInvalido, caminho_da_foto


def test_nome_simples_vira_caminho_no_prefixo_de_fotos() -> None:
    assert caminho_da_foto("dolphin-branco.jpg") == f"{PREFIXO_FOTOS}dolphin-branco.jpg"


@pytest.mark.parametrize(
    "nome",
    [
        "../documentos/espelho-0042.pdf",
        "..%2Fdocumentos%2Fespelho.pdf",
        "sub/dir/foto.jpg",
        "/etc/passwd",
        "..",
        "",
        "   ",
        "foto\\..\\documentos\\x.pdf",
        "foto.jpg/../../documentos/x.pdf",
    ],
)
def test_nome_que_tenta_sair_do_prefixo_e_recusado(nome: str) -> None:
    with pytest.raises(NomeInvalido):
        caminho_da_foto(nome)


def test_o_prefixo_de_documentos_nao_e_alcancavel_pela_rota_de_fotos() -> None:
    """A separação é por prefixo, não por objeto: política por objeto é política que
    alguém esquece de aplicar no objeto seguinte."""
    assert not PREFIXO_DOCUMENTOS.startswith(PREFIXO_FOTOS)
    assert not PREFIXO_FOTOS.startswith(PREFIXO_DOCUMENTOS)


def test_a_rota_publica_recusa_travessia(cliente: TestClient) -> None:
    # Percent-encoded de propósito: `/fotos/..` cru é resolvido pelo cliente HTTP antes de
    # sair, e o servidor nem chega a ver a tentativa. Codificado, ele vê — e recusa.
    for tentativa in ("..%2Fdocumentos%2Fespelho.pdf", "%2E%2E", "a%2Fb.jpg"):
        resposta = cliente.get(f"/fotos/{tentativa}")
        assert resposta.status_code == 404, tentativa
        assert "documentos" not in resposta.text


def test_a_rota_publica_devolve_404_quando_o_arquivo_nao_existe(cliente: TestClient) -> None:
    """Sem MinIO no ambiente de teste a resposta é a mesma de arquivo ausente: a tela do
    catálogo cai no `sem-foto.svg` e a página não quebra por causa de storage fora do ar."""
    assert cliente.get("/fotos/nao-existe.jpg").status_code == 404
