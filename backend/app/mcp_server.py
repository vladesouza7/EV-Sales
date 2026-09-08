"""MCP — configuração da loja e leitura de estoque/atendimento por uma IA externa.

Um cliente MCP (Claude Desktop, Claude Code, etc.) alcança aqui o que a tela
`/configuracoes` já faz — WhatsApp da loja, provedor da LLM, quem recebe aviso, fotos
das unidades — mais leitura de estoque e atendimento. **Nunca mais que isso.**

Fora do escopo, de propósito, e sem tool que dê nem um passo nessa direção:
`buscar_conhecimento` e o resto das tools da Aurora ficam onde estão — isto aqui não é
outro canal de conversa com o cliente. E nenhuma tool altera preço, cria desconto, reserva
chassi ou aprova condição: essas ações moram atrás da fila da Neuza (ADR-004), e um
segundo caminho de acesso seria exatamente a porta que a invariante 2 existe pra fechar.

**Autenticação é uma chave própria, `EVSALES_MCP_CHAVE`** — nunca o login do dono. Todo
tool call passa pela mesma trilha de auditoria da tela (`observabilidade.registrar`),
atribuído ao usuário `dono` cadastrado — é a mesma pessoa que a tela já deixa mexer nisso,
só que por outro canal.
"""

import base64
import binascii
import os
from uuid import UUID

from fastapi import HTTPException
from mcp.server.mcpserver import MCPServer
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app import atendimentos
from app.configuracoes import Entrada, FotoRecusada, Telefones
from app.configuracoes import escrever as _escrever_configuracoes
from app.configuracoes import escrever_telefones as _escrever_telefones
from app.configuracoes import gravar_foto_da_unidade as _gravar_foto_da_unidade
from app.configuracoes import ler_tudo as _ler_configuracoes
from app.configuracoes import listar_unidades_para_foto as _listar_unidades_para_foto
from app.db import Sessao
from app.ia.tools import estoque
from app.modelos import Usuario

VARIAVEL_CHAVE = "EVSALES_MCP_CHAVE"


class SemDono(RuntimeError):
    """Nenhuma tool roda sem um dono cadastrado — é a atribuição da auditoria."""


def _usuario_dono(sessao: Session) -> Usuario:
    dono = sessao.scalars(
        select(Usuario).where(Usuario.perfil == "dono", Usuario.ativo)
    ).first()
    if dono is None:
        raise SemDono(
            "Nenhum usuário dono cadastrado. Rode "
            "'uv --project backend run python scripts/criar-usuario.py' primeiro."
        )
    return dono


def _mensagem_do_erro(erro: HTTPException) -> str:
    detalhe = erro.detail
    if isinstance(detalhe, dict) and "mensagem" in detalhe:
        return str(detalhe["mensagem"])
    return str(detalhe)


servidor: MCPServer = MCPServer(
    name="ev-sales",
    title="EV-Sales — Sol & Volt",
    version="1.0.0",
    instructions=(
        "Configuração da Sol & Volt (WhatsApp, provedor da LLM, quem recebe aviso, fotos "
        "das unidades) e leitura de estoque e atendimentos. Não existe tool de preço, "
        "desconto, reserva ou aprovação — essas ações continuam só na fila da Neuza."
    ),
)


@servidor.tool()
def ler_configuracoes() -> dict[str, object]:
    """Lê o estado atual das configurações: chaves resumidas (segredo nunca em claro,
    só os 4 últimos caracteres), provedores de LLM disponíveis e telefones mascarados."""
    with Sessao() as sessao:
        dono = _usuario_dono(sessao)
        return _ler_configuracoes(sessao, dono)


@servidor.tool()
def salvar_configuracoes(
    valores: dict[str, str] | None = None, limpar: list[str] | None = None
) -> dict[str, object]:
    """Grava configurações — WhatsApp da loja (whatsapp_numero, evolution_url,
    evolution_instancia, evolution_chave) ou provedor da LLM (llm_provedor, llm_modelo,
    llm_url, llm_chave, llm_fallbacks). Mesma sonda da tela: credencial que não funciona
    não grava. `limpar` é a lista de chaves para apagar."""
    with Sessao() as sessao:
        dono = _usuario_dono(sessao)
        entrada = Entrada(valores=valores or {}, limpar=limpar or [])
        try:
            return _escrever_configuracoes(entrada, sessao, dono)
        except HTTPException as erro:
            raise ValueError(_mensagem_do_erro(erro)) from None


@servidor.tool()
def salvar_telefones(
    usuarios: dict[str, str] | None = None, vendedores: dict[str, str] | None = None
) -> dict[str, str]:
    """Troca o telefone de quem recebe aviso da fila de aprovação. As chaves dos dois
    dicionários são o id (UUID) do usuário ou vendedor; o valor é o telefone novo."""
    with Sessao() as sessao:
        dono = _usuario_dono(sessao)
        entrada = Telefones(
            usuarios={UUID(k): v for k, v in (usuarios or {}).items()},
            vendedores={UUID(k): v for k, v in (vendedores or {}).items()},
        )
        try:
            return _escrever_telefones(entrada, sessao, dono)
        except HTTPException as erro:
            raise ValueError(_mensagem_do_erro(erro)) from None


@servidor.tool()
def listar_unidades_para_foto() -> list[dict[str, object]]:
    """Lista todas as unidades (não só as disponíveis) com chassi, marca, modelo, cor,
    condição e a foto atual — para escolher qual chassi recebe uma foto nova."""
    with Sessao() as sessao:
        dono = _usuario_dono(sessao)
        return _listar_unidades_para_foto(sessao, dono)


@servidor.tool()
def subir_foto_da_unidade(chassi: str, conteudo_base64: str, tipo: str) -> dict[str, str]:
    """Troca a foto de uma unidade. `conteudo_base64` é o arquivo (jpg, png ou webp, até
    5 MB) codificado em base64; `tipo` é o content-type (image/jpeg, image/png ou
    image/webp). O nome do objeto no MinIO é sempre o chassi (ADR-013)."""
    try:
        conteudo = base64.b64decode(conteudo_base64, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("conteudo_base64 não é base64 válido.") from None

    with Sessao() as sessao:
        dono = _usuario_dono(sessao)
        try:
            foto_url = _gravar_foto_da_unidade(sessao, dono, chassi, tipo, conteudo)
        except FotoRecusada as erro:
            raise ValueError(str(erro)) from None
    return {"foto_url": foto_url}


@servidor.tool()
def buscar_unidades(
    preco_max_centavos: int | None = None,
    condicao: str | None = None,
    autonomia_min_km: int | None = None,
) -> list[dict[str, object]]:
    """Lista unidades **disponíveis** no pátio — somente leitura, o mesmo filtro do
    catálogo público. Nunca retorna preço de unidade vendida ou reservada."""
    with Sessao() as sessao:
        return estoque.buscar_unidades(
            sessao,
            preco_max_centavos=preco_max_centavos,
            condicao=condicao,
            autonomia_min_km=autonomia_min_km,
        )


@servidor.tool()
def detalhar_unidade(chassi: str) -> dict[str, object]:
    """Ficha completa de um chassi — somente leitura."""
    with Sessao() as sessao:
        ficha = estoque.detalhar_unidade(sessao, chassi)
        if ficha is None:
            raise ValueError(f"Chassi {chassi!r} não encontrado.")
        return ficha


@servidor.tool()
def listar_atendimentos(busca: str = "") -> list[dict[str, object]]:
    """Lista atendimentos por nome, telefone (número exato) ou data — somente leitura,
    sem jargão técnico, a mesma tela que o dono já usa."""
    with Sessao() as sessao:
        dono = _usuario_dono(sessao)
        return atendimentos.listar(sessao, dono, busca)


@servidor.tool()
def ler_atendimento(conversa_id: str) -> dict[str, object]:
    """A conversa inteira de um atendimento, em ordem, com aprovação e verificação
    numérica intercaladas — somente leitura."""
    with Sessao() as sessao:
        dono = _usuario_dono(sessao)
        try:
            return atendimentos.ler(UUID(conversa_id), sessao, dono)
        except HTTPException as erro:
            raise ValueError(_mensagem_do_erro(erro)) from None


@servidor.tool()
def custo_do_mes() -> dict[str, object]:
    """Custo faturado do mês corrente: quatro números e o acumulado por dia — somente
    leitura, a mesma conta que a tela `/custo` mostra."""
    with Sessao() as sessao:
        dono = _usuario_dono(sessao)
        return atendimentos.custo_do_mes(sessao, dono)


class _ExigirChaveMCP(BaseHTTPMiddleware):
    """Sem OAuth, sem servidor de autorização — uma chave só, como o webhook da Evolution
    já faz. Falha fechado: sem `EVSALES_MCP_CHAVE` no ambiente, nenhum pedido passa."""

    async def dispatch(
        self, requisicao: Request, chamar_proximo: RequestResponseEndpoint
    ) -> Response:
        chave = os.environ.get(VARIAVEL_CHAVE, "")
        cabecalho = requisicao.headers.get("authorization", "")
        if not chave or cabecalho != f"Bearer {chave}":
            return JSONResponse(
                {"erro": "chave do MCP ausente ou incorreta"}, status_code=401
            )
        return await chamar_proximo(requisicao)


def construir_app() -> Starlette:
    """Uma instância nova a cada chamada, de propósito.

    O `StreamableHTTPSessionManager` de dentro do `streamable_http_app()` só aceita rodar
    uma vez por instância — a segunda vez que o lifespan do FastAPI reinicia (o teste
    seguinte com `TestClient`, um reload) ele recusa com `RuntimeError`. `MontagemMCP`
    guarda a instância atual e pede uma nova a cada início de lifespan (`main.py`), então
    o mount em si nunca muda — só o que ele encaminha.
    """
    app = servidor.streamable_http_app(streamable_http_path="/")
    app.add_middleware(_ExigirChaveMCP)
    return app


class MontagemMCP:
    """ASGI fino que encaminha para a instância atual — ver `construir_app`."""

    def __init__(self) -> None:
        self.app: Starlette | None = None

    def renovar(self) -> Starlette:
        self.app = construir_app()
        return self.app

    async def __call__(self, scope: object, receive: object, send: object) -> None:
        if self.app is None:
            self.renovar()
        assert self.app is not None
        await self.app(scope, receive, send)  # type: ignore[arg-type]


montagem = MontagemMCP()
