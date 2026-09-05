"""Tabelas do EV-Sales. Nomes em português: a equipe da Sol & Volt lê este código."""

import uuid
from collections.abc import Callable

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.pii import decifrar, mascarar_nome, mascarar_telefone
from app.db import Base, agora

ETAPAS = (
    "saudacao", "qualificacao", "recomendacao", "objecao", "condicao",
    "aguardando_aprovacao", "reserva", "test_drive", "humano", "encerrada",
)  # fmt: skip
STATUS_UNIDADE = ("disponivel", "reservado", "vendido", "indisponivel")
# S-03 §1. Fonte fora desta lista não entra: WLTP e Inmetro só não se confundem
# enquanto o rótulo tiver uma grafia só (invariante 6).
FONTES_DE_AUTONOMIA = ("INMETRO_PBEV_2026", "WLTP", "FABRICANTE")


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    nome_cifrado: Mapped[bytes] = mapped_column(LargeBinary)
    telefone_cifrado: Mapped[bytes] = mapped_column(LargeBinary)
    telefone_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    origem: Mapped[str] = mapped_column(String(32), default="landing")
    criado_em: Mapped[object] = mapped_column(DateTime(timezone=True), default=agora)
    ultimo_acesso_em: Mapped[object] = mapped_column(DateTime(timezone=True), default=agora)

    def __repr__(self) -> str:
        """S-09 §2 — o descuido mais comum (`logger.info(f"{lead}")`) já sai protegido.

        Nunca levanta: um `__repr__` que estoura dentro do `logging` derruba o registro
        inteiro, e é justamente no log que esta proteção precisa funcionar. Objeto
        recém-construído, chave ausente ou blob corrompido saem como `?`, não como erro.
        """
        return f"<Lead {self.id} {self._mascarado(mascarar_nome, self.nome_cifrado)} " + (
            f"{self._mascarado(mascarar_telefone, self.telefone_cifrado)}>"
        )

    @staticmethod
    def _mascarado(mascarar: Callable[[str], str], blob: bytes | None) -> str:
        if not blob:
            return "?"
        try:
            return mascarar(decifrar(blob))
        except Exception:
            return "?"


class Conversa(Base):
    __tablename__ = "conversas"
    __table_args__ = (
        CheckConstraint(f"etapa IN {ETAPAS}", name="ck_conversas_etapa"),
        CheckConstraint("canal_atual IN ('web', 'whatsapp')", name="ck_conversas_canal"),
        CheckConstraint("modo IN ('aurora', 'humano')", name="ck_conversas_modo"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    etapa: Mapped[str] = mapped_column(String(24), default="saudacao")
    canal_atual: Mapped[str] = mapped_column(String(10), default="web")
    modo: Mapped[str] = mapped_column(String(10), default="aurora")
    atendente_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    qualificacao: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)
    chassi_em_foco: Mapped[str | None] = mapped_column(String(17), default=None)
    trace_id: Mapped[str | None] = mapped_column(String(64), default=None)
    token_sessao: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    token_expira_em: Mapped[object] = mapped_column(DateTime(timezone=True))
    criada_em: Mapped[object] = mapped_column(DateTime(timezone=True), default=agora)
    ultima_mensagem_em: Mapped[object | None] = mapped_column(DateTime(timezone=True), default=None)


class Unidade(Base):
    """Não é SKU com quantidade: é um chassi. Não existe 'outro igual'."""

    __tablename__ = "unidades"
    __table_args__ = (
        CheckConstraint(f"status IN {STATUS_UNIDADE}", name="ck_unidades_status"),
        CheckConstraint("condicao IN ('novo', 'seminovo')", name="ck_unidades_condicao"),
        CheckConstraint("preco_centavos > 0", name="ck_unidades_preco_positivo"),
        # ADR-003: número sem fonte confirmada entra NULL, nunca estimado.
        CheckConstraint(
            "(autonomia_km IS NULL) = (autonomia_fonte IS NULL)",
            name="ck_unidades_autonomia_sempre_com_fonte",
        ),
        CheckConstraint(
            f"autonomia_fonte IS NULL OR autonomia_fonte IN {FONTES_DE_AUTONOMIA}",
            name="ck_unidades_fonte_de_autonomia_conhecida",
        ),
    )

    chassi: Mapped[str] = mapped_column(String(17), primary_key=True)
    marca: Mapped[str] = mapped_column(String(40))
    modelo: Mapped[str] = mapped_column(String(60))
    versao: Mapped[str] = mapped_column(String(60))
    ano: Mapped[int] = mapped_column(Integer)
    cor: Mapped[str] = mapped_column(String(30))
    condicao: Mapped[str] = mapped_column(String(10))
    km: Mapped[int] = mapped_column(Integer, default=0)
    preco_centavos: Mapped[int] = mapped_column(BigInteger)
    autonomia_km: Mapped[int | None] = mapped_column(Integer, default=None)
    autonomia_fonte: Mapped[str | None] = mapped_column(String(30), default=None)
    foto_url: Mapped[str | None] = mapped_column(String(200), default=None)
    status: Mapped[str] = mapped_column(String(14), default="disponivel", index=True)
    reservado_para: Mapped[uuid.UUID | None] = mapped_column(default=None)
    reservado_em: Mapped[object | None] = mapped_column(DateTime(timezone=True), default=None)
