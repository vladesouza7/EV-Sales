"""S-12 — critérios de aceite das configurações.

O que se testa aqui é o que a tela **não** pode fazer: devolver segredo, apagar valor por
campo vazio, gravar credencial recusada, travar quando o provedor cai, e deixar o valor
cair na trilha. O caminho feliz é uma linha; o resto do arquivo é o contorno dele.
"""

import json
import urllib.request

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import configuracoes as modulo
from app.autenticacao import criar_usuario
from app.configuracao import Chave, ambiente, esquecer, gravar, valor
from app.core import http
from app.ia.turno import provedor_atual
from app.modelos import Configuracao, Trilha, Unidade, Usuario

SENHA = "senha-de-teste-12"
NUMERO_DA_LOJA = "+5583991575299"
CHAVE_FALSA = "sk-teste-0000000000000000000000004f2a"


@pytest.fixture
def rai(sessao: Session) -> Usuario:
    return criar_usuario(sessao, nome="Raí Sol", email="rai@solevolt.com.br",
                         senha=SENHA, perfil="dono")  # fmt: skip


@pytest.fixture
def neuza(sessao: Session) -> Usuario:
    return criar_usuario(sessao, nome="Neuza Andrade", email="neuza@solevolt.com.br",
                         senha=SENHA, perfil="gerente")  # fmt: skip


def _entrar(cliente: TestClient, usuario: Usuario) -> None:
    entrada = {"email": usuario.email, "senha": SENHA}
    assert cliente.post("/api/entrar", json=entrada).status_code == 200


def _salvar(cliente: TestClient, **valores: str) -> object:
    return cliente.put("/api/configuracoes", json={"valores": valores})


@pytest.fixture
def sonda_aprova(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nenhum teste fala com a rede. A sonda de verdade tem os testes dela, abaixo."""
    monkeypatch.setattr(modulo, "buscar", lambda *_, **__: 200)


@pytest.fixture
def completa(sessao: Session, rai: Usuario) -> None:
    """Provedor e modelo gravados, senão a sonda nem chega a sair — e o teste passaria
    verde por "configuração incompleta", que é o motivo errado."""
    gravar(sessao, Chave.llm_provedor, "openrouter", rai)
    gravar(sessao, Chave.llm_modelo, "fabricante/modelo-1", rai)


# ── §1 quem entra ────────────────────────────────────────────────────────────────


def test_sem_sessao_nao_alcanca(cliente: TestClient) -> None:
    assert cliente.get("/api/configuracoes").status_code == 401


def test_gerente_recebe_404(cliente: TestClient, neuza: Usuario) -> None:
    """404 e não 403: 403 conta que a tela existe (S-11 §5)."""
    _entrar(cliente, neuza)
    resposta = cliente.get("/api/configuracoes")
    assert resposta.status_code == 404
    assert resposta.json() == {"detail": {"mensagem": "Não encontrado."}}


def test_dono_entra(cliente: TestClient, rai: Usuario) -> None:
    _entrar(cliente, rai)
    assert cliente.get("/api/configuracoes").status_code == 200


# ── §4 o segredo não volta ───────────────────────────────────────────────────────


def test_o_segredo_volta_so_com_os_quatro_ultimos(
    cliente: TestClient, sessao: Session, rai: Usuario
) -> None:
    gravar(sessao, Chave.llm_chave, CHAVE_FALSA, rai)
    _entrar(cliente, rai)

    corpo = cliente.get("/api/configuracoes").text
    assert "••••4f2a" in corpo
    assert CHAVE_FALSA not in corpo
    assert "0000000000" not in corpo


def test_campo_vazio_nao_apaga(
    cliente: TestClient, sessao: Session, rai: Usuario, sonda_aprova: None
) -> None:
    gravar(sessao, Chave.llm_chave, CHAVE_FALSA, rai)
    _entrar(cliente, rai)

    assert cliente.put("/api/configuracoes", json={"valores": {"llm_chave": ""}}).status_code == 200
    assert valor(sessao, Chave.llm_chave) == CHAVE_FALSA


def test_limpar_e_explicito(
    cliente: TestClient, sessao: Session, rai: Usuario, sonda_aprova: None
) -> None:
    gravar(sessao, Chave.llm_fallbacks, "a,b", rai)
    _entrar(cliente, rai)

    assert cliente.put("/api/configuracoes", json={"limpar": ["llm_fallbacks"]}).status_code == 200
    assert valor(sessao, Chave.llm_fallbacks) == ""


# ── §2 validação ─────────────────────────────────────────────────────────────────


def test_numero_da_loja_obedece_a_regra_do_cliente(
    cliente: TestClient, sessao: Session, rai: Usuario, sonda_aprova: None
) -> None:
    """A instância recebe a primeira mensagem do cliente: tem que ser celular (ADR-005)."""
    _entrar(cliente, rai)

    assert _salvar(cliente, whatsapp_numero="558391575299").status_code == 422  # type: ignore[attr-defined]
    assert valor(sessao, Chave.whatsapp_numero) == ""

    assert _salvar(cliente, whatsapp_numero=NUMERO_DA_LOJA).status_code == 200  # type: ignore[attr-defined]
    assert valor(sessao, Chave.whatsapp_numero) == NUMERO_DA_LOJA


def test_url_de_esquema_estranho_e_recusada(
    cliente: TestClient, rai: Usuario, sonda_aprova: None
) -> None:
    _entrar(cliente, rai)
    for url in ("file:///etc/passwd", "gopher://interno", "//interno/api"):
        resposta = cliente.put("/api/configuracoes", json={"valores": {"evolution_url": url}})
        assert resposta.status_code == 422, url


# ── §5 a sonda ───────────────────────────────────────────────────────────────────


def test_credencial_recusada_nao_grava(
    cliente: TestClient,
    sessao: Session,
    rai: Usuario,
    completa: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(modulo, "buscar", lambda *_, **__: 401)
    _entrar(cliente, rai)

    assert _salvar(cliente, llm_chave=CHAVE_FALSA).status_code == 422  # type: ignore[attr-defined]
    assert valor(sessao, Chave.llm_chave) == ""


def test_provedor_fora_do_ar_nao_tranca_a_tela(
    cliente: TestClient,
    sessao: Session,
    rai: Usuario,
    completa: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tratar indisponibilidade como recusa travaria a tela na hora de trocar de provedor."""
    monkeypatch.setattr(modulo, "buscar", lambda *_, **__: 503)
    _entrar(cliente, rai)

    resposta = _salvar(cliente, llm_chave=CHAVE_FALSA)
    assert resposta.status_code == 200  # type: ignore[attr-defined]
    assert resposta.json()["aviso"]  # type: ignore[attr-defined]
    assert valor(sessao, Chave.llm_chave) == CHAVE_FALSA


def test_rede_fora_tambem_grava(
    cliente: TestClient,
    sessao: Session,
    rai: Usuario,
    completa: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def cair(*_: object, **__: object) -> int:
        raise http.Indisponivel("DNS")

    monkeypatch.setattr(modulo, "buscar", cair)
    _entrar(cliente, rai)

    assert _salvar(cliente, llm_chave=CHAVE_FALSA).status_code == 200  # type: ignore[attr-defined]
    assert valor(sessao, Chave.llm_chave) == CHAVE_FALSA


def test_a_sonda_da_evolution_valida_a_chave_e_nao_a_instancia(
    cliente: TestClient, sessao: Session, rai: Usuario, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Conferido contra a v2.3.7: `connectionState` de instância inexistente devolve 404
    com chave certa e com chave errada. Sondar por ele impediria salvar a configuração
    antes de criar a instância — que é a ordem em que a loja vai fazer isso."""
    visitadas: list[str] = []

    def anotar(url: str, *_: object, **__: object) -> int:
        visitadas.append(url)
        return 200

    monkeypatch.setattr(modulo, "buscar", anotar)
    _entrar(cliente, rai)

    cliente.put(
        "/api/configuracoes",
        json={
            "valores": {
                "evolution_url": "http://evolution:8080",
                "evolution_instancia": "solevolt",
                "evolution_chave": "chave-de-teste",
            }
        },
    )
    assert visitadas == ["http://evolution:8080/instance/fetchInstances"]


def test_a_sonda_nao_segue_redirecionamento() -> None:
    """É assim que uma URL externa vira interna (S-12 §5)."""
    redirecionadores = [
        m for m in http.abridor().handlers
        if isinstance(m, urllib.request.HTTPRedirectHandler)
    ]
    assert redirecionadores, "sem handler nenhum o urllib reinstala o padrão"
    assert all(isinstance(m, http.SemRedirecionamento) for m in redirecionadores)
    assert http.SemRedirecionamento().redirect_request() is None


def test_redirecionamento_conta_como_recusa(
    cliente: TestClient,
    sessao: Session,
    rai: Usuario,
    completa: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(modulo, "buscar", lambda *_, **__: 302)
    _entrar(cliente, rai)

    assert _salvar(cliente, llm_chave=CHAVE_FALSA).status_code == 422  # type: ignore[attr-defined]
    assert valor(sessao, Chave.llm_chave) == ""


def test_a_sonda_nao_devolve_o_corpo_do_destino(
    cliente: TestClient, rai: Usuario, completa: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Devolver o corpo faria da tela um leitor de qualquer endereço que o servidor alcança."""
    monkeypatch.setattr(modulo, "buscar", lambda *_, **__: 401)
    _entrar(cliente, rai)

    corpo = cliente.post(
        "/api/configuracoes/testar", json={"valores": {"llm_chave": CHAVE_FALSA}}
    ).text
    assert "segredo-do-destino" not in corpo
    assert CHAVE_FALSA not in corpo


# ── §6 precedência ───────────────────────────────────────────────────────────────


def test_banco_vence_env(sessao: Session, rai: Usuario, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVSALES_MODELO", "do-env")
    assert ambiente(sessao)["EVSALES_MODELO"] == "do-env"

    gravar(sessao, Chave.llm_modelo, "do-banco", rai)
    assert ambiente(sessao)["EVSALES_MODELO"] == "do-banco"


def test_a_troca_vale_no_turno_seguinte(
    sessao: Session, rai: Usuario, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sem isto, salvar na tela só valeria depois de reiniciar a API — que é o passo que
    esta spec existe para eliminar."""
    monkeypatch.setattr("app.ia.turno.PROVEDOR", None)
    gravar(sessao, Chave.llm_modelo, "modelo-antigo", rai)
    assert provedor_atual(sessao).modelo == "modelo-antigo"  # type: ignore[attr-defined]

    gravar(sessao, Chave.llm_modelo, "modelo-novo", rai)
    assert provedor_atual(sessao).modelo == "modelo-novo"  # type: ignore[attr-defined]


# ── §7 rastro ────────────────────────────────────────────────────────────────────


def test_trocar_deixa_rastro_sem_deixar_o_valor(sessao: Session, rai: Usuario) -> None:
    gravar(sessao, Chave.llm_chave, CHAVE_FALSA, rai)

    linhas = sessao.scalars(select(Trilha).where(Trilha.nome == "configuracao_alterada")).all()
    assert len(linhas) == 1
    assert linhas[0].dados["chave"] == "llm_chave"
    assert linhas[0].dados["usuario_id"] == str(rai.id)
    assert "4f2a" not in json.dumps(linhas[0].dados, ensure_ascii=False)


# ── §3 quem recebe aviso ─────────────────────────────────────────────────────────


def test_telefone_de_quem_recebe_aviso_sai_mascarado(
    cliente: TestClient, sessao: Session, rai: Usuario, neuza: Usuario
) -> None:
    _entrar(cliente, rai)

    gravado = cliente.put(
        "/api/configuracoes/telefones",
        json={"usuarios": {str(neuza.id): "(83) 99157-5299"}},
    )
    assert gravado.status_code == 200

    corpo = cliente.get("/api/configuracoes").text
    assert "(83) *****-5299" in corpo
    assert "991575299" not in corpo
    assert "99157-5299" not in corpo


def test_telefone_invalido_e_recusado(
    cliente: TestClient, sessao: Session, rai: Usuario, neuza: Usuario
) -> None:
    _entrar(cliente, rai)
    resposta = cliente.put(
        "/api/configuracoes/telefones", json={"usuarios": {str(neuza.id): "3244-1010"}}
    )
    assert resposta.status_code == 422
    sessao.expire_all()
    assert sessao.get(Usuario, neuza.id).telefone_cifrado is None  # type: ignore[union-attr]


# ── §5 foto de unidade ───────────────────────────────────────────────────────────

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
def seal(sessao: Session) -> Unidade:
    unidade = Unidade(**SEAL_BRANCO)  # type: ignore[arg-type]
    sessao.add(unidade)
    sessao.commit()
    return unidade


def test_lista_de_unidades_exige_dono(cliente: TestClient, neuza: Usuario, seal: Unidade) -> None:
    assert cliente.get("/api/configuracoes/unidades").status_code == 401
    _entrar(cliente, neuza)
    assert cliente.get("/api/configuracoes/unidades").status_code == 404


def test_dono_lista_unidades_para_foto(
    cliente: TestClient, rai: Usuario, seal: Unidade
) -> None:
    _entrar(cliente, rai)
    corpo = cliente.get("/api/configuracoes/unidades").json()
    assert len(corpo) == 1
    assert corpo[0]["chassi"] == seal.chassi
    assert corpo[0]["foto_url"] is None


def test_subir_foto_grava_no_minio_e_aponta_a_unidade(
    cliente: TestClient, sessao: Session, rai: Usuario, seal: Unidade
) -> None:
    from app.arquivos import ler

    _entrar(cliente, rai)
    resposta = cliente.post(
        f"/api/configuracoes/unidades/{seal.chassi}/foto",
        content=b"bytes-de-foto-fake",
        headers={"Content-Type": "image/jpeg"},
    )
    assert resposta.status_code == 200
    assert resposta.json() == {"foto_url": f"/fotos/{seal.chassi}.jpg"}

    sessao.expire_all()
    assert sessao.get(Unidade, seal.chassi).foto_url == f"/fotos/{seal.chassi}.jpg"  # type: ignore[union-attr]
    guardado = ler(f"fotos/{seal.chassi}.jpg")
    assert guardado is not None
    assert guardado[0] == b"bytes-de-foto-fake"


def test_subir_foto_tipo_nao_aceito_e_recusado(
    cliente: TestClient, rai: Usuario, seal: Unidade
) -> None:
    _entrar(cliente, rai)
    resposta = cliente.post(
        f"/api/configuracoes/unidades/{seal.chassi}/foto",
        content=b"nao e imagem",
        headers={"Content-Type": "text/plain"},
    )
    assert resposta.status_code == 422


def test_subir_foto_maior_que_5mb_e_recusada(
    cliente: TestClient, rai: Usuario, seal: Unidade
) -> None:
    _entrar(cliente, rai)
    resposta = cliente.post(
        f"/api/configuracoes/unidades/{seal.chassi}/foto",
        content=b"x" * (5 * 1024 * 1024 + 1),
        headers={"Content-Type": "image/jpeg"},
    )
    assert resposta.status_code == 422


def test_subir_foto_de_chassi_inexistente_e_404(cliente: TestClient, rai: Usuario) -> None:
    _entrar(cliente, rai)
    resposta = cliente.post(
        "/api/configuracoes/unidades/NAOEXISTE1234567/foto",
        content=b"bytes",
        headers={"Content-Type": "image/jpeg"},
    )
    assert resposta.status_code == 404


def test_gerente_nao_sobe_foto(cliente: TestClient, neuza: Usuario, seal: Unidade) -> None:
    _entrar(cliente, neuza)
    resposta = cliente.post(
        f"/api/configuracoes/unidades/{seal.chassi}/foto",
        content=b"bytes",
        headers={"Content-Type": "image/jpeg"},
    )
    assert resposta.status_code == 404


def test_linha_com_chave_girada_nao_tranca_a_tela(
    cliente: TestClient, sessao: Session, rai: Usuario
) -> None:
    """Rodar `gerar-segredos.sh` de novo deixa toda linha de `configuracoes` ilegível.

    Se isso derrubasse a tela, a única forma de regravar a credencial seria a tela que
    não abre — e o conserto viraria `psql`. Vale por qualquer blob corrompido.
    """
    gravar(sessao, Chave.whatsapp_numero, NUMERO_DA_LOJA, rai)
    linha = sessao.scalars(
        select(Configuracao).where(Configuracao.chave == Chave.whatsapp_numero.value)
    ).one()
    linha.valor_cifrado = b"\x00" * 40  # o que a chave antiga deixa para trás
    sessao.commit()
    esquecer()

    _entrar(cliente, rai)
    resposta = cliente.get("/api/configuracoes")

    assert resposta.status_code == 200
    assert valor(sessao, Chave.whatsapp_numero) == ""  # cai no `.env`, não estoura
