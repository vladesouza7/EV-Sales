"""ADR-008 — OpenRouter atrás de uma interface do EV-Sales.

A interface existe para que trocar de provedor seja uma tarde, e não uma refatoração no
meio de um incidente. `ProvedorLLM` é o contrato; `OpenRouterLLM` é a única implementação
do v1.

Duas escolhas que parecem detalhe e não são:

**Sem streaming do provedor.** A S-02 §3 já exige que a resposta seja gerada inteira,
verificada e só então fatiada — texto reprovado não pode ter saído nem parcialmente. Um
cliente de streaming aqui seria complexidade a serviço de um buffer que a arquitetura
manda encher de qualquer jeito.

**`urllib` da biblioteca padrão, não um cliente HTTP novo.** É um POST JSON com timeout,
e o fallback entre fabricantes — que o ADR-008 pede — é um campo do payload do OpenRouter,
não lógica de cliente. ponytail: vira `httpx` no dia em que precisar de pool de conexão
ou streaming, e nesse dia o resto deste arquivo não muda.
"""

import asyncio
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol

URL = "https://openrouter.ai/api/v1/chat/completions"
TEMPO_LIMITE_S = 30
TOKENS_MAXIMOS = 4_000  # S-03 §6
TEMPERATURA = 0.3  # "a Aurora conversa, não escreve poesia" (ADR-008)

# ADR-008: o modelo é fixado no `.env`, por id exato, nunca por alias que aponta para "o
# mais novo" — alias muda a qualidade da Aurora sem um commit meu. Sem `EVSALES_MODELO`
# configurado o provedor não existe, e o turno degrada para humano em vez de adivinhar.
VARIAVEL_MODELO = "EVSALES_MODELO"
VARIAVEL_CHAVE = "EVSALES_OPENROUTER_API_KEY"
VARIAVEL_FALLBACKS = "EVSALES_MODELOS_FALLBACK"
VARIAVEL_DOLAR = "EVSALES_DOLAR_CENTAVOS"


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
    tools: list[ChamadaDeTool] = field(default_factory=list)


class ProvedorLLM(Protocol):
    """O contrato. Mensagens e tools entram, texto, tool calls e custo saem."""

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
    centavos = int(os.environ.get(VARIAVEL_DOLAR, "550"))
    return centavos * 10_000


class OpenRouterLLM:
    """Única implementação do v1. Ler o `.env` no construtor, e não no módulo, é o que
    deixa o teste trocar a configuração sem reimportar nada."""

    def __init__(self) -> None:
        self.chave = os.environ.get(VARIAVEL_CHAVE, "")
        self.modelo = os.environ.get(VARIAVEL_MODELO, "")
        self.fallbacks = [
            m.strip() for m in os.environ.get(VARIAVEL_FALLBACKS, "").split(",") if m.strip()
        ]

    def configurado(self) -> bool:
        return bool(self.chave and self.modelo)

    def _payload(
        self, mensagens: list[dict[str, object]], tools: list[dict[str, object]]
    ) -> dict[str, object]:
        corpo: dict[str, object] = {
            "model": self.modelo,
            "messages": mensagens,
            "temperature": TEMPERATURA,
            "max_tokens": TOKENS_MAXIMOS,
            # ADR-006: o custo gravado é o faturado. Sem isto o `usage` não traz `cost`,
            # e a alternativa seria um tokenizer local estimando o que o Raí paga.
            "usage": {"include": True},
            # ADR-007 / ADR-008: só provedores com retenção zero e sem treino sobre os
            # dados. É esta linha que torna o OpenRouter aceitável para a conversa de um
            # cliente da Sol & Volt.
            "provider": {"data_collection": "deny"},
        }
        if self.fallbacks:
            # O fallback entre fabricantes é campo do payload, não `try/except` meu.
            corpo["models"] = [self.modelo, *self.fallbacks]
        if tools:
            corpo["tools"] = tools
        return corpo

    def _postar(self, corpo: dict[str, object]) -> dict[str, object]:
        requisicao = urllib.request.Request(
            URL,
            data=json.dumps(corpo).encode(),
            headers={
                "Authorization": f"Bearer {self.chave}",
                "Content-Type": "application/json",
                "X-Title": "EV-Sales — Sol & Volt",
            },
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

    async def conversar(
        self, mensagens: list[dict[str, object]], tools: list[dict[str, object]]
    ) -> Resposta:
        # `to_thread` porque o POST é bloqueante e o turno roda dentro do gerador do SSE:
        # segurar o event loop aqui pararia o stream de todas as outras conversas.
        dados = await asyncio.to_thread(self._postar, self._payload(mensagens, tools))
        mensagem = _primeira_mensagem(dados)

        return Resposta(
            texto=str(mensagem.get("content") or ""),
            modelo=str(dados.get("model") or self.modelo),
            custo_micro_reais=round(_custo_em_dolar(dados) * _dolar_em_micro_reais()),
            tools=_chamadas_de_tool(mensagem),
        )


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
