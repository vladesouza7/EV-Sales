"""ADR-012 — o provedor é configuração, e a configuração é conferível sem rede.

Nenhum teste aqui abre socket: o que se testa é o que muda entre provedores — URL,
header, campos extras do payload e se o custo é faturado. A qualidade da resposta de
cada um é o eval da S-03 §8, e é ele que tem autoridade para aprovar uma troca.
"""

import json

import pytest

from app.ia.provedor import PRESETS, ProvedorCompativel


def _configurar(monkeypatch: pytest.MonkeyPatch, **variaveis: str) -> ProvedorCompativel:
    apagar = ("EVSALES_PROVEDOR", "EVSALES_LLM_API_KEY", "EVSALES_MODELO", "EVSALES_LLM_URL",
              "EVSALES_MODELOS_FALLBACK")  # fmt: skip
    for nome in apagar:
        monkeypatch.delenv(nome, raising=False)
    for nome, valor in variaveis.items():
        monkeypatch.setenv(nome, valor)
    return ProvedorCompativel()


def test_o_padrao_continua_sendo_o_openrouter(monkeypatch: pytest.MonkeyPatch) -> None:
    provedor = _configurar(monkeypatch, EVSALES_LLM_API_KEY="k", EVSALES_MODELO="algum/modelo")

    assert provedor.configurado()
    assert "openrouter.ai" in provedor.url
    assert provedor.preset.custo_faturado is True


def test_so_o_openrouter_recebe_os_campos_que_so_ele_entende(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`usage.include` e `data_collection: deny` são dele. Mandar para outro é ruído."""
    aberto = _configurar(monkeypatch, EVSALES_LLM_API_KEY="k", EVSALES_MODELO="m")
    local = _configurar(monkeypatch, EVSALES_PROVEDOR="ollama", EVSALES_MODELO="qwen3")

    corpo_aberto = aberto.montar_payload([], [])
    corpo_local = local.montar_payload([], [])

    assert corpo_aberto["usage"] == {"include": True}
    assert corpo_aberto["provider"] == {"data_collection": "deny"}
    assert "usage" not in corpo_local and "provider" not in corpo_local


def test_ollama_nao_exige_chave_porque_nao_tem_para_quem_pagar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provedor = _configurar(monkeypatch, EVSALES_PROVEDOR="ollama", EVSALES_MODELO="qwen3")

    assert provedor.configurado()
    assert "11434" in provedor.url
    assert provedor.cabecalhos().get("Authorization") is None


@pytest.mark.parametrize("nome", ["gemini", "nvidia"])
def test_provedor_de_fabricante_exige_chave_e_nao_fatura(
    monkeypatch: pytest.MonkeyPatch, nome: str
) -> None:
    sem_chave = _configurar(monkeypatch, EVSALES_PROVEDOR=nome, EVSALES_MODELO="m")
    assert not sem_chave.configurado()

    com_chave = _configurar(
        monkeypatch, EVSALES_PROVEDOR=nome, EVSALES_MODELO="m", EVSALES_LLM_API_KEY="k"
    )
    assert com_chave.configurado()
    assert com_chave.preset.custo_faturado is False
    assert com_chave.cabecalhos()["Authorization"] == "Bearer k"


def test_compativel_aponta_para_a_url_que_o_env_disser(monkeypatch: pytest.MonkeyPatch) -> None:
    """A porta aberta do ADR-012 — sem ela, cada provedor novo pediria um ADR."""
    provedor = _configurar(
        monkeypatch,
        EVSALES_PROVEDOR="compativel",
        EVSALES_MODELO="m",
        EVSALES_LLM_URL="http://gpu.solevolt.local:8000/v1",
    )

    assert provedor.configurado()
    assert provedor.url == "http://gpu.solevolt.local:8000/v1/chat/completions"


def test_compativel_sem_url_nao_sobe_pela_metade(monkeypatch: pytest.MonkeyPatch) -> None:
    provedor = _configurar(monkeypatch, EVSALES_PROVEDOR="compativel", EVSALES_MODELO="m")
    assert not provedor.configurado()


def test_provedor_desconhecido_falha_alto_e_nao_cai_no_padrao(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cair no OpenRouter por causa de um erro de digitação seria mandar a conversa do
    cliente para fora da loja sem ninguém ter decidido isso."""
    provedor = _configurar(
        monkeypatch, EVSALES_PROVEDOR="opemrouter", EVSALES_MODELO="m", EVSALES_LLM_API_KEY="k"
    )
    assert not provedor.configurado()


def test_custo_so_e_lido_de_quem_fatura(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provedor que não fatura grava 0 com `custo_faturado=False` — "não sei quanto
    custou" não é a mesma coisa que "custou zero"."""
    resposta_do_provedor = {
        "model": "m",
        "choices": [{"message": {"content": "oi"}}],
        "usage": {"cost": 0.01, "total_tokens": 120},
    }

    aberto = _configurar(monkeypatch, EVSALES_LLM_API_KEY="k", EVSALES_MODELO="m")
    local = _configurar(monkeypatch, EVSALES_PROVEDOR="ollama", EVSALES_MODELO="qwen3")

    do_aberto = aberto.ler_resposta(resposta_do_provedor)
    do_local = local.ler_resposta(resposta_do_provedor)

    assert do_aberto.custo_faturado is True and do_aberto.custo_micro_reais > 0
    assert do_local.custo_faturado is False and do_local.custo_micro_reais == 0


def test_todo_preset_declara_politica_de_dados() -> None:
    """ADR-012: provedor novo entra com a política lida e escrita, nunca por omissão."""
    for nome, preset in PRESETS.items():
        assert preset.politica_de_dados, f"{nome} entrou sem política de dados declarada"


def test_o_fallback_entre_fabricantes_e_campo_do_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    provedor = _configurar(
        monkeypatch,
        EVSALES_LLM_API_KEY="k",
        EVSALES_MODELO="a/1",
        EVSALES_MODELOS_FALLBACK="b/2, c/3",
    )
    corpo = provedor.montar_payload([], [])

    assert corpo["models"] == ["a/1", "b/2", "c/3"]
    assert json.dumps(corpo)


def test_a_url_do_env_e_a_base_e_o_caminho_completo_tambem_serve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Todo provedor documenta a base. Colar o caminho completo também funciona."""
    base = _configurar(
        monkeypatch,
        EVSALES_PROVEDOR="compativel",
        EVSALES_MODELO="m",
        EVSALES_LLM_URL="https://ollama.com/v1",
    )
    completo = _configurar(
        monkeypatch,
        EVSALES_PROVEDOR="compativel",
        EVSALES_MODELO="m",
        EVSALES_LLM_URL="https://ollama.com/v1/chat/completions",
    )

    assert base.url == completo.url == "https://ollama.com/v1/chat/completions"
