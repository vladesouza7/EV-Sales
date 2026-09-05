"""S-02 — chat web e sessão de conversa.

A conversa é a mesma nos dois canais (ADR-005): `canal` é coluna da mensagem, e o
histórico do turno seguinte não sabe por onde a anterior entrou.
"""

import asyncio
import json
import secrets
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import FUSO, Sessao, agora, obter_sessao
from app.ia.turno import executar_turno
from app.limite import dentro_do_limite
from app.modelos import Conversa, Mensagem

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"

LIMITE_CARACTERES = 2_000
TURNOS_MAXIMOS = 60
LIMITE_POR_MINUTO = 12
INTERVALO_DE_ESPERA = 0.2

router = APIRouter()
BancoDeDados = Annotated[Session, Depends(obter_sessao)]

# ponytail: trava por conversa dentro do processo — vale para as duas abas do mesmo
# cliente, que é o caso real. Vira lock no Redis quando a api rodar com mais de um
# worker, junto com o contador da limite.py, que já paga o mesmo preço.
_travas: dict[uuid.UUID, asyncio.Lock] = {}


def conversa_da_sessao(
    conversa_id: uuid.UUID, requisicao: Request, sessao: BancoDeDados
) -> Conversa:
    """O cookie da S-01 é a credencial. Não há login, e o token vale por uma conversa."""
    token = requisicao.cookies.get("ev_sessao")
    conversa = sessao.get(Conversa, conversa_id)
    # 404 e não 403: responder "existe, mas não é sua" já é dizer que existe.
    if conversa is None or not token or not secrets.compare_digest(conversa.token_sessao, token):
        raise HTTPException(404, detail={"mensagem": "Conversa não encontrada."})
    if conversa.token_expira_em < agora():
        raise HTTPException(401, detail={"mensagem": "Sua sessão expirou."})
    return conversa


ConversaAberta = Annotated[Conversa, Depends(conversa_da_sessao)]


class MensagemEntrada(BaseModel):
    conteudo: str


@router.get("/conversas/{conversa_id}", include_in_schema=False)
def pagina_do_chat(conversa_id: uuid.UUID) -> FileResponse:
    return FileResponse(FRONTEND / "chat.html")


@router.get("/api/conversas/{conversa_id}")
def retomar(conversa: ConversaAberta, sessao: BancoDeDados) -> dict[str, object]:
    """S-02 §6 — reabrir a página restaura a conversa com todo o histórico."""
    mensagens = sessao.scalars(
        select(Mensagem)
        .where(Mensagem.conversa_id == conversa.id)
        .order_by(Mensagem.criada_em, Mensagem.id)
    )
    return {
        "conversa_id": str(conversa.id),
        "etapa": conversa.etapa,
        "modo": conversa.modo,
        "mensagens": [
            {
                "id": str(m.id),
                "autor": m.autor,
                "canal": m.canal,
                "conteudo": m.conteudo,
                # America/Fortaleza explícito: o Postgres devolve em UTC.
                "criada_em": m.criada_em.astimezone(FUSO).isoformat(),
            }
            for m in mensagens
        ],
    }


@router.post("/api/conversas/{conversa_id}/mensagens", status_code=201)
def enviar(
    entrada: MensagemEntrada, conversa: ConversaAberta, sessao: BancoDeDados
) -> dict[str, object]:
    conteudo = entrada.conteudo.strip()
    if not conteudo:
        raise HTTPException(400, detail={"mensagem": "Escreve alguma coisa que eu te respondo."})
    if len(conteudo) > LIMITE_CARACTERES:
        raise HTTPException(400, detail={"mensagem": "Essa mensagem é longa demais pro chat."})
    dentro = dentro_do_limite(
        f"conversa:{conversa.id}", maximo=LIMITE_POR_MINUTO, janela_segundos=60
    )
    if not dentro:
        raise HTTPException(429, detail={"mensagem": "Calma aí! Deixa eu responder uma por vez."})

    sessao.add(
        Mensagem(
            conversa_id=conversa.id,
            direcao="entrada",
            autor="cliente",
            canal=conversa.canal_atual,
            conteudo=conteudo,
        )
    )
    conversa.ultima_mensagem_em = agora()
    sessao.flush()
    if _turnos(sessao, conversa.id) >= TURNOS_MAXIMOS:
        # S-02 §4 — passou de 60 turnos, a Aurora sai e um vendedor assume.
        conversa.modo = "humano"
        conversa.etapa = "humano"
    sessao.commit()
    return {"modo": conversa.modo}


@router.post("/api/conversas/{conversa_id}/humano")
def falar_com_pessoa(conversa: ConversaAberta, sessao: BancoDeDados) -> dict[str, object]:
    """O botão da S-02 §5, sempre visível. Vira a tool `transferir_para_humano` na S-03."""
    if conversa.etapa != "encerrada":
        conversa.modo = "humano"
        conversa.etapa = "humano"
        sessao.commit()
    return {"modo": conversa.modo, "etapa": conversa.etapa}


@router.get("/api/conversas/{conversa_id}/stream")
async def stream(conversa: ConversaAberta, requisicao: Request) -> StreamingResponse:
    ultimo = requisicao.headers.get("last-event-id")
    return StreamingResponse(
        _eventos(conversa.id, requisicao, ultimo),
        media_type="text/event-stream",
        # Sem isto o nginx do perfil prod segura o SSE em buffer até o turno acabar.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(evento: str, dados: dict[str, object]) -> str:
    identificador = dados.get("mensagem_id")
    cabecalho = f"id: {identificador}\n" if identificador else ""
    return f"{cabecalho}event: {evento}\ndata: {json.dumps(dados, ensure_ascii=False)}\n\n"


def _turnos(sessao: Session, conversa_id: uuid.UUID) -> int:
    total = sessao.scalar(
        select(func.count())
        .select_from(Mensagem)
        .where(Mensagem.conversa_id == conversa_id, Mensagem.direcao == "entrada")
    )
    return total or 0


def _pendente(sessao: Session, conversa_id: uuid.UUID) -> Mensagem | None:
    """A fila da §4 é a própria tabela, e `limit(1)` é a regra de um turno por vez."""
    return sessao.scalars(
        select(Mensagem)
        .where(
            Mensagem.conversa_id == conversa_id,
            Mensagem.direcao == "entrada",
            Mensagem.processada_em.is_(None),
        )
        .order_by(Mensagem.criada_em, Mensagem.id)
        .limit(1)
    ).one_or_none()


def _repetir_desde(sessao: Session, conversa_id: uuid.UUID, ultimo: str | None) -> Iterator[str]:
    """Reconexão por `Last-Event-ID` (§3).

    A queda não perde mensagem porque o estado está no Postgres, e o que o cliente já
    viu está identificado pelo id da mensagem que fechou o último turno.
    """
    if not ultimo:
        return
    try:
        marco = sessao.get(Mensagem, uuid.UUID(ultimo))
    except ValueError:
        return
    if marco is None:
        return
    perdidas = sessao.scalars(
        select(Mensagem)
        .where(
            Mensagem.conversa_id == conversa_id,
            Mensagem.direcao == "saida",
            Mensagem.criada_em > marco.criada_em,
        )
        .order_by(Mensagem.criada_em, Mensagem.id)
    )
    for mensagem in perdidas:
        yield _sse("token", {"texto": mensagem.conteudo})
        yield _sse("mensagem_fim", {"mensagem_id": str(mensagem.id), "etapa": "retomada"})


async def _eventos(
    conversa_id: uuid.UUID, requisicao: Request, ultimo: str | None
) -> AsyncIterator[str]:
    # Sessão própria: a do Depends fecha quando a resposta começa, e este gerador vive
    # enquanto a aba estiver aberta.
    with Sessao() as sessao:
        for repetido in _repetir_desde(sessao, conversa_id, ultimo):
            yield repetido

        avisou_espera = False
        while not await requisicao.is_disconnected():
            sessao.rollback()  # encerra a transação anterior: o POST escreve por outra
            conversa = sessao.get(Conversa, conversa_id)
            if conversa is None:
                return

            pendente = _pendente(sessao, conversa_id)
            if pendente is not None and conversa.modo == "aurora":
                trava = _travas.setdefault(conversa_id, asyncio.Lock())
                async with trava:
                    async for nome, dados in executar_turno(sessao, conversa, pendente):
                        yield _sse(nome, dados)
                continue
            if pendente is not None:
                # Modo humano: a Aurora não responde (§2). A mensagem fica pro vendedor.
                pendente.processada_em = agora()
                sessao.commit()

            if conversa.etapa == "aguardando_aprovacao" and not avisou_espera:
                avisou_espera = True
                yield _sse("aguardando", {"motivo": "aprovacao"})

            yield ": ping\n\n"
            await asyncio.sleep(INTERVALO_DE_ESPERA)
