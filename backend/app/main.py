"""S-01 — landing da Sol & Volt, captura de lead e catálogo somente-leitura."""

import logging
import secrets
from datetime import timedelta
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.pii import cifrar, hash_telefone, mascarar_telefone
from app.core.validacao import normalizar_nome, normalizar_telefone
from app.db import agora, obter_sessao
from app.ia.tools.estoque import buscar_unidades
from app.limite import dentro_do_limite
from app.modelos import Conversa, Lead

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
VALIDADE_SESSAO = timedelta(hours=24)
LIMITE_IP = (5, 10 * 60)
LIMITE_CONVERSAS_POR_HORA = 3

# Sob o uvicorn o root fica sem handler em INFO, e a linha mascarada da S-09 nunca sairia.
# ponytail: basicConfig resolve; a configuração de verdade (formato, Langfuse) é da S-08.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger("evsales")

app = FastAPI(title="EV-Sales — Sol & Volt")
app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

BancoDeDados = Annotated[Session, Depends(obter_sessao)]


class LeadEntrada(BaseModel):
    nome: str
    telefone: str
    origem: str = "landing"

    @field_validator("nome")
    @classmethod
    def _nome(cls, bruto: str) -> str:
        return normalizar_nome(bruto)

    @field_validator("telefone")
    @classmethod
    def _telefone(cls, bruto: str) -> str:
        return normalizar_telefone(bruto)


@app.get("/", include_in_schema=False)
def landing() -> FileResponse:
    return FileResponse(FRONTEND / "index.html")


@app.get("/catalogo", include_in_schema=False)
def pagina_catalogo() -> FileResponse:
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
    if not dentro_do_limite(f"ip:{ip}", maximo=LIMITE_IP[0], janela_segundos=LIMITE_IP[1]):
        raise HTTPException(429, detail={"mensagem": "Muitos cadastros deste dispositivo agora."})

    telefone_hash = hash_telefone(entrada.telefone)
    lead = sessao.scalars(select(Lead).where(Lead.telefone_hash == telefone_hash)).one_or_none()

    if lead is None:
        lead = Lead(
            nome_cifrado=cifrar(entrada.nome),
            telefone_cifrado=cifrar(entrada.telefone),
            telefone_hash=telefone_hash,
            origem=entrada.origem,
        )
        sessao.add(lead)
        sessao.flush()
    else:
        lead.ultimo_acesso_em = agora()
        _recusar_se_ja_conversa_demais(sessao, lead)

    conversa = Conversa(
        lead_id=lead.id,
        etapa="saudacao",
        token_sessao=secrets.token_urlsafe(32),
        token_expira_em=agora() + VALIDADE_SESSAO,
    )
    sessao.add(conversa)
    sessao.commit()

    resposta.set_cookie(
        "ev_sessao",
        conversa.token_sessao,
        httponly=True,
        samesite="lax",
        max_age=int(VALIDADE_SESSAO.total_seconds()),
    )
    logger.info(
        "lead %s · conversa %s · origem %s",
        mascarar_telefone(entrada.telefone),
        conversa.id,
        entrada.origem,
    )
    return {"conversa_id": str(conversa.id), "token_sessao": conversa.token_sessao}


def _recusar_se_ja_conversa_demais(sessao: Session, lead: Lead) -> None:
    """S-01 §5 — 3 conversas por hora, por telefone. O contador é o próprio banco."""
    recentes = sessao.scalars(
        select(Conversa)
        .where(Conversa.lead_id == lead.id, Conversa.criada_em > agora() - timedelta(hours=1))
        .order_by(Conversa.criada_em.desc())
    ).all()
    if len(recentes) >= LIMITE_CONVERSAS_POR_HORA:
        raise HTTPException(
            429,
            detail={
                "mensagem": "Você já tem uma conversa aberta com a Aurora. Continue por lá.",
                "conversa_id": str(recentes[0].id),
            },
        )
