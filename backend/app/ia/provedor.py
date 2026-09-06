"""ADR-008 e ADR-012 — o provedor de LLM atrás de uma interface do EV-Sales.

O 008 escolheu o OpenRouter e escreveu a promessa: trocar de provedor é uma tarde, não uma
refatoração no meio de um incidente. O 012 cobra a promessa e transforma a escolha em
configuração — sem virar quatro integrações, porque **Ollama, NVIDIA NIM e Gemini falam o
protocolo da OpenAI**: mesmo `/chat/completions`, mesmos `tools`, mesmo `tool_calls` de volta.

O que muda entre eles cabe numa tabela: URL, header, campos extras e **se o custo é
faturado**. O último não é detalhe — é o que separa "custou zero" de "não sei quanto custou",
e sem essa distinção o teto do Raí para de proteger em silêncio (ADR-006).

Duas escolhas que parecem detalhe e não são:

**Sem streaming do provedor.** A S-02 §3 já exige que a resposta seja gerada inteira,
verificada e só então fatiada — texto reprovado não pode ter saído nem parcialmente. Um
cliente de streaming aqui seria complexidade a serviço de um buffer que a arquitetura manda
encher de qualquer jeito.

**`urllib` da biblioteca padrão, não um cliente HTTP novo.** É um POST JSON com timeout, e o
fallback entre fabricantes — que o ADR-008 pede — é campo do payload do OpenRouter, não
lógica de cliente. ponytail: vira `httpx` no dia em que precisar de pool de conexão ou
streaming, e nesse dia o resto deste arquivo não muda.
"""

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol

logger = logging.getLogger(__name__)

TEMPO_LIMITE_S = 30
TOKENS_MAXIMOS = 4_000  # S-03 §6
TEMPERATURA = 0.3  # "a Aurora conversa, não escreve poesia" (ADR-008)

VARIAVEL_PROVEDOR = "EVSALES_PROVEDOR"
VARIAVEL_CHAVE = "EVSALES_LLM_API_KEY"
VARIAVEL_URL = "EVSALES_LLM_URL"
VARIAVEL_MODELO = "EVSALES_MODELO"
VARIAVEL_FALLBACKS = "EVSALES_MODELOS_FALLBACK"
VARIAVEL_DOLAR = "EVSALES_DOLAR_CENTAVOS"

PROVEDOR_PADRAO = "openrouter"


@dataclass(frozen=True)
class Preset:
    """Tudo o que difere entre provedores. Se algo não está aqui, não difere."""

    url: str
    # ADR-012: provedor entra com a política lida e escrita, nunca por omissão. Um teste
    # exige que este campo esteja preenchido em todo preset.
    politica_de_dados: str
    exige_chave: bool = True
    custo_faturado: bool = False
    extras: dict[str, object] = field(default_factory=dict)
    cabecalhos: dict[str, str] = field(default_factory=dict)


PRESETS: dict[str, Preset] = {
    "openrouter": Preset(
        url="https://openrouter.ai/api/v1/chat/completions",
        politica_de_dados=(
            "Roteamento restrito a provedores com retenção zero e sem treino sobre os dados."
        ),
        custo_faturado=True,
        # ADR-006: sem `usage.include` não vem `cost`, e a alternativa seria um tokenizer
        # local estimando o que o Raí paga. ADR-007: é o `data_collection` que torna o
        # OpenRouter aceitável para a conversa de um cliente da Sol & Volt.
        extras={"usage": {"include": True}, "provider": {"data_collection": "deny"}},
        cabecalhos={"X-Title": "EV-Sales — Sol & Volt"},
    ),
    "ollama": Preset(
        url="http://localhost:11434/v1/chat/completions",
        politica_de_dados="A conversa não sai da máquina da loja. Elimina a superfície do ADR-007.",
        exige_chave=False,
    ),
    "gemini": Preset(
        url="https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        politica_de_dados="Sujeita aos termos do Google. Verificar antes de produção com cliente.",
    ),
    "nvidia": Preset(
        url="https://integrate.api.nvidia.com/v1/chat/completions",
        politica_de_dados="Sujeita aos termos da NVIDIA. Verificar antes de produção com cliente.",
    ),
    "compativel": Preset(
        url="",  # vem de EVSALES_LLM_URL; sem ela o provedor não sobe
        politica_de_dados="Depende de para onde a URL aponta. Verificar antes de produção.",
        exige_chave=False,
    ),
}

# Nome fora da tabela não cai no padrão: mandar a conversa para fora da loja por causa de
# um erro de digitação no `.env` não é degradação, é vazamento.
_DESCONHECIDO = Preset(url="", politica_de_dados="")


@dataclass(frozen=True)
class ChamadaDeTool:
    id: str
    nome: str
    argumentos: dict[str, object]


@dataclass(frozen=True)
class Resposta:
    texto: str
    modelo: str
    custo_micro_reais: int
    # ADR-012: `0` com `custo_faturado=False` é "não sei quanto custou". Só o Ollama tem
    # as duas coisas verdadeiras ao mesmo tempo.
    custo_faturado: bool = True
    tools: list[ChamadaDeTool] = field(default_factory=list)


class ProvedorLLM(Protocol):
    """O contrato. Mensagens e tools entram; texto, tool calls e custo saem."""

    def configurado(self) -> bool: ...

    async def conversar(
        self, mensagens: list[dict[str, object]], tools: list[dict[str, object]]
    ) -> Resposta: ...


class ProvedorIndisponivel(RuntimeError):
    """O provedor não respondeu. Quem chama degrada para humano, não inventa resposta."""


def _dolar_em_micro_reais() -> int:
    """Câmbio fixado no `.env`, revisado por commit.

    ponytail: o `usage` do OpenRouter vem em dólar e o teto do Raí é em real. Cotação em
    tempo real seria mais uma dependência de rede no caminho do turno para mover um número
    que o Raí revisa uma vez por trimestre.
    """
    return int(os.environ.get(VARIAVEL_DOLAR, "550")) * 10_000


class ProvedorCompativel:
    """Qualquer provedor que fale o protocolo da OpenAI (ADR-012).

    Ler o `.env` no construtor, e não no módulo, é o que deixa o teste trocar a
    configuração sem reimportar nada.
    """

    def __init__(self) -> None:
        self.nome = os.environ.get(VARIAVEL_PROVEDOR, PROVEDOR_PADRAO)
        self.conhecido = self.nome in PRESETS
        self.preset = PRESETS.get(self.nome, _DESCONHECIDO)
        self.chave = os.environ.get(VARIAVEL_CHAVE, "")
        self.modelo = os.environ.get(VARIAVEL_MODELO, "")
        self.fallbacks = [
            m.strip() for m in os.environ.get(VARIAVEL_FALLBACKS, "").split(",") if m.strip()
        ]
        # A URL do `.env` vence o preset: é como o Ollama roda com outro host no compose.
        self.url = os.environ.get(VARIAVEL_URL, "") or self.preset.url

    def configurado(self) -> bool:
        if not self.conhecido:
            logger.error("%s=%r não é um provedor conhecido", VARIAVEL_PROVEDOR, self.nome)
            return False
        if self.preset.exige_chave and not self.chave:
            return False
        return bool(self.modelo and self.url)

    def cabecalhos(self) -> dict[str, str]:
        cabecalhos = {"Content-Type": "application/json", **self.preset.cabecalhos}
        if self.chave:
            cabecalhos["Authorization"] = f"Bearer {self.chave}"
        return cabecalhos

    def montar_payload(
        self, mensagens: list[dict[str, object]], tools: list[dict[str, object]]
    ) -> dict[str, object]:
        corpo: dict[str, object] = {
            "model": self.modelo,
            "messages": mensagens,
            "temperature": TEMPERATURA,
            "max_tokens": TOKENS_MAXIMOS,
            **self.preset.extras,
        }
        if self.fallbacks:
            # O fallback entre fabricantes é campo do payload, não `try/except` meu.
            corpo["models"] = [self.modelo, *self.fallbacks]
        if tools:
            corpo["tools"] = tools
        return corpo

    def _postar(self, corpo: dict[str, object]) -> dict[str, object]:
        requisicao = urllib.request.Request(
            self.url, data=json.dumps(corpo).encode(), headers=self.cabecalhos()
        )
        try:
            with urllib.request.urlopen(requisicao, timeout=TEMPO_LIMITE_S) as resposta:
                dados: dict[str, object] = json.loads(resposta.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as erro:
            # Sem o corpo do erro na mensagem: a requisição carrega a conversa do cliente,
            # e mensagem de exceção acaba em log (invariante 5).
            raise ProvedorIndisponivel(type(erro).__name__) from None
        if "error" in dados:
            raise ProvedorIndisponivel("resposta de erro do provedor")
        return dados

    def ler_resposta(self, dados: dict[str, object]) -> Resposta:
        mensagem = _primeira_mensagem(dados)
        faturado = self.preset.custo_faturado
        custo = round(_custo_em_dolar(dados) * _dolar_em_micro_reais()) if faturado else 0
        return Resposta(
            texto=str(mensagem.get("content") or ""),
            modelo=str(dados.get("model") or self.modelo),
            custo_micro_reais=custo,
            custo_faturado=faturado,
            tools=_chamadas_de_tool(mensagem),
        )

    async def conversar(
        self, mensagens: list[dict[str, object]], tools: list[dict[str, object]]
    ) -> Resposta:
        # `to_thread` porque o POST é bloqueante e o turno roda dentro do gerador do SSE:
        # segurar o event loop aqui pararia o stream de todas as outras conversas.
        dados = await asyncio.to_thread(self._postar, self.montar_payload(mensagens, tools))
        return self.ler_resposta(dados)


def _primeira_mensagem(dados: dict[str, object]) -> dict[str, object]:
    """JSON de terceiro é fronteira de confiança: nada aqui assume formato.

    Um `KeyError` no meio do turno vira 500 no SSE e conversa perdida; um dicionário
    vazio vira resposta sem texto, que o chamador já sabe tratar como indisponibilidade.
    """
    escolhas = dados.get("choices")
    if not isinstance(escolhas, list) or not escolhas or not isinstance(escolhas[0], dict):
        return {}
    mensagem = escolhas[0].get("message")
    return mensagem if isinstance(mensagem, dict) else {}


def _custo_em_dolar(dados: dict[str, object]) -> float:
    uso = dados.get("usage")
    custo = uso.get("cost") if isinstance(uso, dict) else None
    return float(custo) if isinstance(custo, int | float) else 0.0


def _chamadas_de_tool(mensagem: dict[str, object]) -> list[ChamadaDeTool]:
    brutas = mensagem.get("tool_calls")
    if not isinstance(brutas, list):
        return []
    chamadas = []
    for bruta in brutas:
        if not isinstance(bruta, dict):
            continue
        funcao = bruta.get("function")
        funcao = funcao if isinstance(funcao, dict) else {}
        try:
            argumentos = json.loads(funcao.get("arguments") or "{}")
        except json.JSONDecodeError:
            # Modelo que erra a formatação dos argumentos não derruba o turno: a tool
            # não roda, e o loop segue sem o retorno dela.
            argumentos = {}
        chamadas.append(
            ChamadaDeTool(
                id=str(bruta.get("id", "")),
                nome=str(funcao.get("name", "")),
                argumentos=argumentos if isinstance(argumentos, dict) else {},
            )
        )
    return chamadas
