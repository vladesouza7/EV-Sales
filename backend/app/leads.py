"""S-01 §2 a §5 — captura de lead e abertura de conversa.

Mora fora do `main` porque há duas portas para a mesma coisa: a landing e a página de
test drive da S-07. Duplicar cifragem, hash e limite em duas rotas é como a invariante 5
se perde — a segunda cópia é sempre a que esquece de mascarar.
"""

import logging
import secrets
from datetime import timedelta

from fastapi import HTTPException, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.pii import cifrar, hash_telefone, mascarar_telefone
from app.core.validacao import normalizar_nome, normalizar_telefone
from app.db import agora
from app.limite import dentro_do_limite
from app.modelos import Conversa, Lead

VALIDADE_SESSAO = timedelta(hours=24)
LIMITE_IP = (5, 10 * 60)
LIMITE_CONVERSAS_POR_HORA = 3

logger = logging.getLogger("evsales")


class LeadEntrada(BaseModel):
    nome: str
    telefone: str
    origem: str = Field("landing", max_length=32)
    # S-01 §6: veio do catálogo por "falar com a Aurora sobre este".
    interesse: str | None = Field(None, max_length=17)

    @field_validator("nome")
    @classmethod
    def _nome(cls, bruto: str) -> str:
        return normalizar_nome(bruto)

    @field_validator("telefone")
    @classmethod
    def _telefone(cls, bruto: str) -> str:
        return normalizar_telefone(bruto)


def abrir_conversa(sessao: Session, entrada: LeadEntrada, ip: str) -> Conversa:
    """Cria ou reaproveita o lead pelo `telefone_hash` e abre uma conversa nova."""
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
        # A recusa vem antes da escrita: levantar depois descartaria o ultimo_acesso_em
        # junto com a transação, e a S-01 §4.2 exige que ele seja atualizado.
        _recusar_se_ja_conversa_demais(sessao, lead)
        lead.ultimo_acesso_em = agora()

    conversa = Conversa(
        lead_id=lead.id,
        etapa="saudacao",
        chassi_em_foco=entrada.interesse,
        token_sessao=secrets.token_urlsafe(32),
        token_expira_em=agora() + VALIDADE_SESSAO,
    )
    sessao.add(conversa)
    sessao.commit()

    logger.info(
        "lead %s · conversa %s · origem %s",
        mascarar_telefone(entrada.telefone),
        conversa.id,
        entrada.origem,
    )
    return conversa


def gravar_cookie(resposta: Response, conversa: Conversa) -> None:
    resposta.set_cookie(
        "ev_sessao",
        conversa.token_sessao,
        httponly=True,
        samesite="lax",
        max_age=int(VALIDADE_SESSAO.total_seconds()),
    )


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
