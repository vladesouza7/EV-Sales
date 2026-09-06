"""Tabelas do EV-Sales. Nomes em português: a equipe da Sol & Volt lê este código."""

import uuid
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    literal_column,
)
from sqlalchemy.dialects.postgresql import JSONB, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.pii import decifrar, mascarar_nome, mascarar_telefone
from app.db import Base, agora

ETAPAS = (
    "saudacao", "qualificacao", "recomendacao", "objecao", "condicao",
    "aguardando_aprovacao", "reserva", "test_drive", "humano", "encerrada",
)  # fmt: skip
STATUS_UNIDADE = ("disponivel", "reservado", "vendido", "indisponivel")
STATUS_TEST_DRIVE = ("agendado", "confirmado", "realizado", "nao_compareceu", "cancelado")
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
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    ultimo_acesso_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)

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
    # S-07 §4.4 — o que a conversa virou. Só o test drive preenche por enquanto.
    desfecho: Mapped[str | None] = mapped_column(String(30), default=None)
    trace_id: Mapped[str | None] = mapped_column(String(64), default=None)
    token_sessao: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    token_expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    criada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    ultima_mensagem_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )


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
    reservado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class Mensagem(Base):
    """S-02 §1 — `canal` é coluna da mensagem, não da conversa.

    É o que faz o handoff do ADR-005 funcionar sem migração de sessão: a conversa
    troca de canal e mantém id, histórico e qualificação.
    """

    __tablename__ = "mensagens"
    __table_args__ = (
        CheckConstraint("direcao IN ('entrada', 'saida')", name="ck_mensagens_direcao"),
        CheckConstraint(
            "autor IN ('cliente', 'aurora', 'vendedor')", name="ck_mensagens_autor"
        ),
        CheckConstraint("canal IN ('web', 'whatsapp')", name="ck_mensagens_canal"),
        # A fila de turnos da S-02 §4 é esta tabela: só o que entrou pode estar pendente.
        CheckConstraint(
            "direcao = 'entrada' OR processada_em IS NOT NULL",
            name="ck_mensagens_saida_nunca_pendente",
        ),
        Index("ix_mensagens_conversa_criada", "conversa_id", "criada_em"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Sem índice próprio: o composto abaixo já cobre a busca por conversa.
    conversa_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversas.id", ondelete="CASCADE"))
    direcao: Mapped[str] = mapped_column(String(8))
    autor: Mapped[str] = mapped_column(String(10))
    canal: Mapped[str] = mapped_column(String(10), default="web")
    conteudo: Mapped[str] = mapped_column(Text)
    gerada_por_ia: Mapped[bool] = mapped_column(Boolean, default=False)
    whatsapp_message_id: Mapped[str | None] = mapped_column(String(64), default=None)
    payload_bruto: Mapped[dict[str, object] | None] = mapped_column(JSONB, default=None)
    criada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    # ponytail: a fila de turnos é esta coluna, não um broker. Vira fila de verdade
    # quando houver mais de um worker consumindo a mesma conversa.
    processada_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )


class Vendedor(Base):
    """Tarcísio e Jaqueline. O telefone é PII como qualquer outro (ADR-007)."""

    __tablename__ = "vendedores"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    nome: Mapped[str] = mapped_column(String(60), unique=True)
    telefone_cifrado: Mapped[bytes] = mapped_column(LargeBinary)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)


class AgendaBloqueio(Base):
    """Férias, folga, compromisso pessoal. Some da agenda como se fosse test drive."""

    __tablename__ = "agenda_bloqueios"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    vendedor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vendedores.id", ondelete="CASCADE"), index=True
    )
    inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fim: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    motivo: Mapped[str] = mapped_column(String(60))


class TestDrive(Base):
    """S-07 §1 — a garantia de não haver dois no mesmo horário é do banco, não do Python.

    Os dois `EXCLUDE` abaixo são o motivo de a S-07 existir como spec e não como
    formulário: um `SELECT` que confere e um `INSERT` depois deixam a janela aberta
    entre os dois. O índice fecha a janela dentro da transação, como a reserva de
    chassi da S-05 (ADR-001). A consulta de horários em Python é só para **oferecer** —
    quem **decide** é a constraint.
    """

    __tablename__ = "test_drives"
    __table_args__ = (
        CheckConstraint(f"status IN {STATUS_TEST_DRIVE}", name="ck_test_drives_status"),
        CheckConstraint("fim > inicio", name="ck_test_drives_intervalo_positivo"),
        # Cancelado não ocupa horário — senão desmarcar não devolveria a vaga.
        ExcludeConstraint(
            (literal_column("vendedor_id"), "="),
            (literal_column("tstzrange(inicio, fim)"), "&&"),
            name="ex_test_drives_vendedor",
            using="gist",
            where=literal_column("status <> 'cancelado'"),
        ),
        ExcludeConstraint(
            (literal_column("chassi"), "="),
            (literal_column("tstzrange(inicio, fim)"), "&&"),
            name="ex_test_drives_chassi",
            using="gist",
            where=literal_column("status <> 'cancelado'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversa_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversas.id", ondelete="CASCADE"))
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    chassi: Mapped[str] = mapped_column(ForeignKey("unidades.chassi"))
    vendedor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vendedores.id"))
    inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    fim: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="agendado")
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    confirmado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class Trilha(Base):
    """S-08 §1 — a trilha de auditoria: um turno, uma tool, uma verificação, um evento.

    É a **origem** das duas leituras do ADR-006: a tela do Raí lê daqui as linhas `⚙` e
    `⏸`, e eu leio daqui a latência e o custo. Fica no Postgres, e não só no Langfuse,
    porque a tela do Raí é produto — depender da API de uma ferramenta de terceiro para
    renderizar a conversa dele seria trocar o dado por um serviço.

    ponytail: exportar cada linha para o Langfuse é um `for` sobre esta tabela; entra
    quando o container entrar (S-10). O que não pode mudar é onde a PII é mascarada —
    aqui, na montagem, nunca na configuração da ferramenta (ADR-006, ADR-007).
    """

    __tablename__ = "trilha"
    __table_args__ = (
        CheckConstraint(
            "tipo IN ('turno', 'tool', 'verificacao', 'evento')", name="ck_trilha_tipo"
        ),
        CheckConstraint("custo_micro_reais >= 0", name="ck_trilha_custo_nao_negativo"),
        Index("ix_trilha_conversa_criado", "conversa_id", "criado_em"),
        Index("ix_trilha_criado", "criado_em"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # SET NULL, não CASCADE: apagar o lead pela retenção da S-09 §6 não pode apagar o
    # gasto do mês. O que some é o vínculo com a pessoa, não o número que o Raí pagou.
    conversa_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversas.id", ondelete="SET NULL"), default=None
    )
    tipo: Mapped[str] = mapped_column(String(12))
    nome: Mapped[str] = mapped_column(String(40))
    dados: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)
    duracao_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    # Micro-reais, não centavos: um turno custa fração de centavo, e arredondar turno a
    # turno erraria o total do mês por mais do que a margem do teto. `bigint` inteiro
    # continua valendo (CLAUDE.md) — o que muda é a escala, não o tipo.
    custo_micro_reais: Mapped[int] = mapped_column(BigInteger, default=0)
    # ADR-012: provedor que não fatura grava 0 aqui e `false` aqui embaixo. Sem esta
    # coluna, o painel do Raí leria R$ 0,00 para uma API que ele está pagando, e o teto
    # de R$ 900 deixaria de proteger sem dizer nada.
    custo_faturado: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)


class Incidente(Base):
    """S-08 §6 — incidente que só existe em trace é incidente que ninguém revisa."""

    __tablename__ = "incidentes"
    __table_args__ = (
        CheckConstraint(
            "gravidade IN ('critica', 'alta', 'baixa', 'informativa')",
            name="ck_incidentes_gravidade",
        ),
        Index("ix_incidentes_tipo_criado", "tipo", "criado_em"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversa_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversas.id", ondelete="SET NULL"), default=None
    )
    tipo: Mapped[str] = mapped_column(String(30))
    gravidade: Mapped[str] = mapped_column(String(12))
    dados: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)


class Usuario(Base):
    """S-11 §1 — quatro pessoas nomeadas numa loja em Tambaú.

    Quatro é o número que justifica **não** ter cadastro público, convite por e-mail, SSO
    nem papel configurável em banco. Cada uma dessas coisas seria código a manter para um
    problema que a Sol & Volt não tem.

    Não há tabela de sessões: a sessão é um JWT de 20 minutos, e a revogação é o carimbo
    `sessoes_validas_apos` comparado com o `iat` do token (S-11 §2 e §4).
    """

    __tablename__ = "usuarios"
    __table_args__ = (
        CheckConstraint(
            "perfil IN ('dono', 'gerente', 'vendedor')", name="ck_usuarios_perfil"
        ),
        # O vendedor é o único perfil que atende lead, e o recorte da S-11 §5 depende deste
        # vínculo existir. Deixar a regra em Python permitiria um vendedor sem vendedor_id,
        # e o `WHERE vendedor_id = :usuario` devolveria a lista vazia em silêncio.
        CheckConstraint(
            "(perfil = 'vendedor') = (vendedor_id IS NOT NULL)",
            name="ck_usuarios_vendedor_tem_vinculo",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    nome: Mapped[str] = mapped_column(String(60))
    email: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    senha_hash: Mapped[str] = mapped_column(String(200))
    perfil: Mapped[str] = mapped_column(String(10))
    vendedor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("vendedores.id", ondelete="RESTRICT"), default=None
    )
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    senha_trocada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    tentativas_falhas: Mapped[int] = mapped_column(Integer, default=0)
    bloqueado_ate: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    # Token emitido antes deste instante é recusado. É a revogação inteira, em uma coluna
    # — e ela é por usuário, não por dispositivo (S-11 §2, consequência aceita).
    sessoes_validas_apos: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    def __repr__(self) -> str:
        """Sem e-mail: é identificador pessoal, e `logger.info(f"{usuario}")` acontece."""
        return f"<Usuario {self.id} {self.perfil}>"
