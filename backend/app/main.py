"""Aplicação da Sol & Volt: S-01 (landing, lead, catálogo), S-02 (chat) e S-07 (agenda)."""

import logging
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.conversas import router as rotas_de_conversa
from app.db import obter_sessao
from app.ia.tools.estoque import buscar_unidades
from app.leads import LeadEntrada, abrir_conversa, gravar_cookie
from app.testdrive import router as rotas_de_test_drive

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"

# Sob o uvicorn o root fica sem handler em INFO, e a linha mascarada da S-09 nunca sairia.
# ponytail: basicConfig resolve; a configuração de verdade (formato, Langfuse) é da S-08.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

app = FastAPI(title="EV-Sales — Sol & Volt")
app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
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

    ponytail: Redis, OpenRouter e a instância da Evolution entram aqui quando entrarem
    no projeto (S-03, S-06). Um item fixo em "não configurado" seria checagem que nunca
    falha, e checagem que nunca falha é ruído.
    """
    try:
        sessao.execute(text("SELECT 1"))
    except Exception:
        # Sem `str(erro)`: a URL do banco carrega a senha, e o corpo de erro é uma das
        # quatro superfícies que a invariante 5 nomeia.
        resposta.status_code = 503
        return {"postgres": "indisponivel"}
    return {"postgres": "ok"}


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
