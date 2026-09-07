"""Aplicação da Sol & Volt: S-01 (landing, lead, catálogo), S-02 (chat) e S-07 (agenda)."""

import logging
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.responses import Response as RespostaCrua
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.aprovacao import router as rotas_de_aprovacao
from app.arquivos import NomeInvalido, caminho_da_foto, ler
from app.atendimentos import router as rotas_de_atendimento
from app.autenticacao import Autenticado
from app.autenticacao import router as rotas_de_autenticacao
from app.configuracoes import router as rotas_de_configuracao
from app.conversas import router as rotas_de_conversa
from app.core.pii import decifrar
from app.db import agora, obter_sessao
from app.ia.tools.estoque import buscar_unidades
from app.ia.turno import provedor_atual
from app.leads import LeadEntrada, abrir_conversa, gravar_cookie
from app.modelos import Conversa, Lead, PedidoDeAprovacao
from app.observabilidade import registrar
from app.testdrive import router as rotas_de_test_drive

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"

# Sob o uvicorn o root fica sem handler em INFO, e a linha mascarada da S-09 nunca sairia.
# ponytail: basicConfig resolve; a configuração de verdade (formato, Langfuse) é da S-08.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

app = FastAPI(title="EV-Sales — Sol & Volt")
app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
app.include_router(rotas_de_autenticacao)
app.include_router(rotas_de_aprovacao)
app.include_router(rotas_de_atendimento)
app.include_router(rotas_de_configuracao)
app.include_router(rotas_de_conversa)
app.include_router(rotas_de_test_drive)

BancoDeDados = Annotated[Session, Depends(obter_sessao)]


@app.exception_handler(RequestValidationError)
async def erro_sem_eco_de_pii(_: Request, erro: RequestValidationError) -> JSONResponse:
    """Invariante 5 — o corpo de erro é uma das quatro superfícies que ela nomeia.

    O Pydantic devolve, por padrão, um campo `input` com o que o cliente digitou: um
    422 de telefone inválido carregaria o telefone em claro para qualquer coisa que
    capture resposta de erro (trace, Sentry, log de proxy). Aqui só sai a mensagem.
    """
    return JSONResponse(
        status_code=422,
        content={
            "detail": [
                {"loc": list(e["loc"]), "msg": e["msg"].removeprefix("Value error, ")}
                for e in erro.errors()
            ]
        },
    )


@app.get("/fotos/{nome}", include_in_schema=False)
def foto(nome: str) -> RespostaCrua:
    """ADR-013 — a única rota onde nome vindo da internet vira caminho de objeto.

    Serve **exclusivamente** o prefixo `fotos/`. Sem a validação em `caminho_da_foto`,
    `/fotos/../documentos/espelho-0042.pdf` seria uma rota pública para o documento com o
    nome do cliente — e nenhuma proteção do ADR-007 alcança um arquivo servido assim.

    Nome inválido e arquivo ausente devolvem o mesmo 404: distinguir os dois contaria a
    quem sonda que o prefixo de documentos existe.
    """
    try:
        conteudo = ler(caminho_da_foto(nome))
    except NomeInvalido:
        conteudo = None
    if conteudo is None:
        return RespostaCrua(status_code=404)
    bytes_, tipo = conteudo
    # Foto de carro não muda; o catálogo é a página mais aberta do site.
    return RespostaCrua(bytes_, media_type=tipo, headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/leads/{lead_id}/telefone")
def telefone_do_lead(
    lead_id: uuid.UUID, usuario: Autenticado, sessao: BancoDeDados
) -> dict[str, str]:
    """S-11 §6 e S-09 §5 — o terceiro chamador autorizado de `decifrar`.

    O recorte do vendedor é `WHERE`, não filtro de tela: a consulta já sai do banco
    restrita, porque filtrar depois de ler significa que o lead do outro chegou a existir
    no processo — e é assim que um dia ele aparece num log de erro.
    """
    consulta = select(Lead).where(Lead.id == lead_id)
    if usuario.perfil == "vendedor":
        consulta = consulta.join(Conversa, Conversa.lead_id == Lead.id).where(
            Conversa.atendente_id == usuario.vendedor_id
        )
    lead = sessao.scalars(consulta).first()
    if lead is None:
        # 404 também para "existe, mas não é seu".
        raise HTTPException(404, detail={"mensagem": "Não encontrado."})

    # A linha de auditoria registra o ACESSO. O número não entra nela: guardar o telefone
    # dentro do registro de quem viu o telefone seria uma segunda cópia da PII, no lugar
    # mais fácil de esquecer (S-11 §6).
    registrar(
        sessao,
        None,
        "evento",
        "telefone_visto",
        dados={"usuario_id": str(usuario.id), "lead_id": str(lead.id)},
    )
    return {"telefone": decifrar(lead.telefone_cifrado)}


@app.get("/health", include_in_schema=False)
def saude() -> dict[str, str]:
    """Liveness: o processo está de pé, e nada além disso.

    Não toca em dependência de propósito — um liveness que consulta o banco reinicia a
    api quando quem caiu foi o Postgres, que é o oposto do que se quer no incidente.
    """
    return {"status": "ok"}


@app.get("/health/ready", include_in_schema=False)
def saude_das_dependencias(sessao: BancoDeDados, resposta: Response) -> dict[str, str]:
    """S-08 §7 — readiness. Só o que existe hoje é conferido.

    O provedor aparece como `configurado`, e não como `ok`: daqui só dá para afirmar que
    a chave, o modelo e a URL estão no ambiente. Dizer "ok" seria afirmar que ele responde,
    o que exigiria gastar uma chamada de verdade a cada readiness. O nome do provedor sai
    junto porque, com o ADR-012, qual deles está ligado é informação de operação.

    ponytail: Redis e a instância da Evolution entram quando entrarem no projeto (S-06).

    Só o Postgres derruba o readiness: sem provedor a Aurora degrada para atendimento
    humano, que é operação reduzida e não indisponibilidade.
    """
    # Lê o provedor efetivo, não o do import: depois da S-12 a configuração pode ter
    # mudado sem reinício, e um readiness que responde pelo valor antigo mente.
    provedor = provedor_atual(sessao)
    nome = getattr(provedor, "nome", "?")
    situacao = "configurado" if provedor.configurado() else "nao_configurado"
    estado = {"llm": f"{nome}: {situacao}"}
    try:
        sessao.execute(text("SELECT 1"))
    except Exception:
        # Sem `str(erro)`: a URL do banco carrega a senha, e o corpo de erro é uma das
        # quatro superfícies que a invariante 5 nomeia.
        resposta.status_code = 503
        return {**estado, "postgres": "indisponivel"}
    return {**estado, "postgres": "ok"}


@app.get("/atendimentos", include_in_schema=False)
def pagina_de_atendimentos() -> FileResponse:
    return FileResponse(FRONTEND / "atendimentos.html")


@app.get("/custo", include_in_schema=False)
def pagina_de_custo() -> FileResponse:
    return FileResponse(FRONTEND / "custo.html")


@app.get("/configuracoes", include_in_schema=False)
def pagina_de_configuracoes() -> FileResponse:
    """Pública como as outras: o que ela mostra é que não é (S-12 §1)."""
    return FileResponse(FRONTEND / "configuracoes.html")


@app.get("/entrar", include_in_schema=False)
def pagina_de_login() -> FileResponse:
    return FileResponse(FRONTEND / "entrar.html")


@app.get("/aprovacoes", include_in_schema=False)
def pagina_de_aprovacoes() -> FileResponse:
    """A tela é pública; o que ela mostra não é.

    Quem barra é a API: sem sessão, `/api/aprovacoes` devolve 401 e a página manda para o
    login guardando o destino. Proteger o HTML também não acrescentaria nada — ele não
    contém dado nenhum.
    """
    return FileResponse(FRONTEND / "aprovacoes.html")


@app.get("/a/{codigo}", include_in_schema=False)
def atalho_de_aprovacao(codigo: str, sessao: BancoDeDados) -> RedirectResponse:
    """S-04 §3 e S-11 §7 — o link da notificação da Neuza.

    Leva ao card certo e **não substitui o login**: quem confere a sessão é a API da
    página de destino. Código inválido ou já usado cai na fila, sem contar qual dos dois
    aconteceu.
    """
    pedido = sessao.scalars(
        select(PedidoDeAprovacao).where(
            PedidoDeAprovacao.codigo == codigo,
            PedidoDeAprovacao.codigo_usado_em.is_(None),
        )
    ).one_or_none()
    if pedido is None:
        return RedirectResponse("/aprovacoes", status_code=303)
    pedido.codigo_usado_em = agora()
    sessao.commit()
    return RedirectResponse(f"/aprovacoes?pedido={pedido.id}", status_code=303)


@app.get("/", include_in_schema=False)
def landing() -> FileResponse:
    return FileResponse(FRONTEND / "index.html")


@app.get("/catalogo", include_in_schema=False)
def pagina_catalogo() -> FileResponse:
    return FileResponse(FRONTEND / "catalogo.html")


@app.get("/ofertas", include_in_schema=False)
def pagina_ofertas() -> FileResponse:
    """Mesma página do catálogo, outra moldura — os dados são os mesmos e verdadeiros.

    Não há preço promocional: desconto não existe no sistema (invariante 2, ADR-004), e
    "oferta" aqui é ordenação por preço, não abatimento inventado no HTML.
    """
    return FileResponse(FRONTEND / "catalogo.html")


@app.get("/api/catalogo")
def catalogo(sessao: BancoDeDados) -> list[dict[str, object]]:
    """A mesma tool que a Aurora usa (S-01 §6). Nunca uma consulta paralela."""
    return buscar_unidades(sessao)


@app.post("/api/leads", status_code=201)
def criar_lead(
    entrada: LeadEntrada, requisicao: Request, resposta: Response, sessao: BancoDeDados
) -> dict[str, str]:
    # ponytail: IP direto do socket. Ler X-Forwarded-For quando o nginx do perfil prod entrar.
    ip = requisicao.client.host if requisicao.client else "desconhecido"
    conversa = abrir_conversa(sessao, entrada, ip)
    gravar_cookie(resposta, conversa)
    return {"conversa_id": str(conversa.id), "token_sessao": conversa.token_sessao}
