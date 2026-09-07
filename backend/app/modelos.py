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
    Sequence,
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
# S-07 §9 — os quatro desfechos, e cada um decide o status da unidade, da reserva e da
# situação do lead. A tabela dessa decisão mora em `app/testdrive.py`, num lugar só.
DESFECHOS = ("vendeu", "vai_pensar", "desistiu", "nao_compareceu")
SITUACOES_DO_LEAD = ("novo", "em_negociacao", "ganho", "perdido", "a_recontatar")
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
    # S-07 §9 — onde o desfecho do test drive aterrissa. Nasce `novo` e só muda por toque
    # de gente autenticada: nenhuma rotina promove lead a `ganho`.
    situacao: Mapped[str] = mapped_column(String(16), default="novo", server_default="novo")
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

    def nome_mascarado(self) -> str:
        """O nome para tela e log, e **a única forma de ler nome fora dos três chamadores
        autorizados de `decifrar`** (S-09 §3).

        Nunca levanta, pela mesma razão que o `__repr__` não levanta: um registro antigo
        que não decifra — chave girada, blob corrompido — não pode derrubar a fila da
        Neuza nem a tela do Raí inteiras. Um cliente aparece como `?`; os outros oito
        continuam visíveis.
        """
        return self._mascarado(mascarar_nome, self.nome_cifrado)

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
        # S-06 §4.1 — a deduplicação é do banco. A Evolution entrega *at-least-once*, e
        # um `SELECT` antes do `INSERT` reabre a janela para o turno duplicado, que é o
        # mesmo erro que a invariante 4 proíbe na reserva.
        Index(
            "ux_mensagens_whatsapp_id",
            "whatsapp_message_id",
            unique=True,
            postgresql_where=literal_column("whatsapp_message_id IS NOT NULL"),
        ),
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
        CheckConstraint(
            f"desfecho IS NULL OR desfecho IN {DESFECHOS}", name="ck_test_drives_desfecho"
        ),
        # S-07 §9 — "só por toque de vendedor autenticado, com auditoria". Desfecho sem
        # autor e sem hora é recusado pelo banco, não só evitado pelo código: é deste
        # registro que sai `unidades.status = 'vendido'`, que ninguém desfaz depois.
        CheckConstraint(
            "(desfecho IS NULL) = (desfecho_por IS NULL)"
            " AND (desfecho IS NULL) = (desfecho_em IS NULL)",
            name="ck_test_drives_desfecho_tem_autor",
        ),
        Index(
            "ix_test_drives_sem_desfecho",
            "inicio",
            postgresql_where=literal_column("desfecho IS NULL"),
        ),
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
    # S-07 §6 — o lembrete é único, e a rotina roda a cada 5 minutos. Carimbado nos dois
    # casos: mensagem enviada, ou tarefa de ligação criada porque a janela fechou.
    lembrete_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # S-07 §9 — o desfecho, e quem o marcou. O CHECK acima obriga os três juntos: desfecho
    # sem autor e sem hora não é registro, é palpite, e é dele que sai `unidades.vendido`.
    compareceu: Mapped[bool | None] = mapped_column(Boolean, default=None)
    desfecho: Mapped[str | None] = mapped_column(String(16), default=None)
    desfecho_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    desfecho_por: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("usuarios.id", ondelete="RESTRICT"), default=None
    )
    # Quantas vezes o vendedor já foi cobrado (§9, "contra o esquecimento"). Um contador
    # em vez de dois carimbos: a rotina só precisa saber se cabe a próxima cobrança.
    cobrancas: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


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
    # S-12 §3 — para onde vai a notificação da S-04 §3 e o alerta da S-08 §3. Mora aqui,
    # e não em `configuracoes`, porque é telefone de pessoa: uma segunda cópia seria a que
    # a retenção da S-09 §6 esquece de apagar. Nulo para quem não quer receber nada.
    telefone_cifrado: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)
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


class PedidoDeAprovacao(Base):
    """S-04 §1 — a pausa antes do irreversível.

    ARQUIVO DE REVISÃO HUMANA OBRIGATÓRIA na parte de migration (CLAUDE.md): esta tabela e
    `espelhos` são o que garante as invariantes 3 e 4.

    `preco_centavos` é preenchido **pelo servidor**, relendo `unidades.preco_centavos` no
    instante do pedido. Não vem do modelo, não vem do payload da requisição, não passa pelo
    agente (ADR-004).
    """

    __tablename__ = "pedidos_de_aprovacao"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pendente', 'aprovado', 'rejeitado', 'expirado')",
            name="ck_pedidos_status",
        ),
        CheckConstraint("preco_centavos > 0", name="ck_pedidos_preco_positivo"),
        # Decisão sem quem decidiu é decisão sem dono. O par anda junto ou não existe.
        CheckConstraint(
            "(decidido_por IS NULL) = (decidido_em IS NULL)", name="ck_pedidos_decisao_completa"
        ),
        # Uma conversa não tem dois pedidos pendentes. A etapa `aguardando_aprovacao` não
        # tem tools, então a Aurora não consegue pedir duas vezes — mas a garantia é aqui,
        # e não na lista de tools, porque lista de tools é configuração e índice é banco.
        Index(
            "ux_pedidos_um_pendente_por_conversa",
            "conversa_id",
            unique=True,
            postgresql_where=literal_column("status = 'pendente'"),
        ),
        Index("ix_pedidos_status_criado", "status", "criado_em"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversa_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversas.id", ondelete="CASCADE"))
    chassi: Mapped[str] = mapped_column(ForeignKey("unidades.chassi"))
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    preco_centavos: Mapped[int] = mapped_column(BigInteger)
    qualificacao_resumo: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(10), default="pendente")
    decidido_por: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("usuarios.id", ondelete="RESTRICT"), default=None
    )
    decidido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    motivo_rejeicao: Mapped[str | None] = mapped_column(String(40), default=None)
    # S-04 §7 — "quem, quando, de qual IP". O IP não é PII do cliente: é do funcionário.
    ip_da_decisao: Mapped[str | None] = mapped_column(String(45), default=None)
    # S-11 §7 — o link de uso único da notificação. Leva ao card, não substitui o login.
    codigo: Mapped[str] = mapped_column(String(8), unique=True, index=True)
    codigo_usado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    escalado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    trace_id: Mapped[str | None] = mapped_column(String(64), default=None)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# Declarada no metadata, e não só na migration: sem isto o `create_all` do banco de teste
# não a cria, e a numeração do Espelho só quebraria em produção.
NUMERO_DO_ESPELHO = Sequence("espelhos_numero_seq", metadata=Base.metadata)


class Espelho(Base):
    """S-04 §1 — o Espelho de Condição e Reserva. **Não é contrato nem documento fiscal.**

    O nome da tabela é `espelhos`, e não `propostas`, de propósito: "proposta" carregou a
    ambiguidade que originou o ADR-011. Quem implementar não deve construir um documento
    de venda.

    `approval_id` é `NOT NULL` com foreign key, e é a invariante 3 inteira: **não existe
    caminho de código que emita sem aprovação**. Se alguém — eu, ou um agente de código —
    escrever um `INSERT` sem ele, o banco recusa. Um teste tenta exatamente isso.
    """

    __tablename__ = "espelhos"
    __table_args__ = (
        CheckConstraint("preco_centavos > 0", name="ck_espelhos_preco_positivo"),
        # Uma aprovação emite um espelho, não dois.
        Index("ux_espelhos_por_aprovacao", "approval_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversa_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversas.id", ondelete="CASCADE"))
    chassi: Mapped[str] = mapped_column(ForeignKey("unidades.chassi"))
    approval_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pedidos_de_aprovacao.id", ondelete="RESTRICT"), nullable=False
    )
    preco_centavos: Mapped[int] = mapped_column(BigInteger)
    numero: Mapped[str] = mapped_column(String(16), unique=True)
    # O objeto no MinIO, não a URL: a URL é assinada e expira em 15 minutos (ADR-013).
    # Guardar uma URL aqui seria guardar um link morto.
    pdf_objeto: Mapped[str] = mapped_column(String(120))
    valido_ate: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    emitido_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)


class Reserva(Base):
    """S-05 — a operação que não pode falhar.

    ARQUIVO DE REVISÃO HUMANA OBRIGATÓRIA na migration (CLAUDE.md).

    > "Carro eu tenho um de cada. Se esse negócio prometer o mesmo Seal branco pra duas
    > pessoas, alguém vai ter que ligar pra uma delas e desmarcar. E esse alguém sou eu."

    Quem decide a corrida é o `UPDATE … WHERE status = 'disponivel'` em `unidades`, não
    esta tabela. Os dois índices parciais abaixo são a segunda linha: mesmo que alguém
    troque o UPDATE por um SELECT seguido de INSERT — o erro que já aconteceu neste
    repositório —, o banco recusa a segunda reserva ativa.
    """

    __tablename__ = "reservas"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ativa', 'liberada', 'concluida')", name="ck_reservas_status"
        ),
        # S-05 §4 — no máximo 2 renovações, de 72h cada.
        CheckConstraint("renovacoes BETWEEN 0 AND 2", name="ck_reservas_renovacoes"),
        CheckConstraint("expira_em > criada_em", name="ck_reservas_prazo_positivo"),
        # Um chassi tem no máximo uma reserva ativa. Este índice é o que sobrevive a um
        # refactor errado da operação em `app/reserva.py`.
        Index(
            "ux_reservas_uma_ativa_por_chassi",
            "chassi",
            unique=True,
            postgresql_where=literal_column("status = 'ativa'"),
        ),
        # S-05 §2 — o lead não tem outra reserva ativa. Reservar dois carros é o começo
        # de dois carros parados, e o Raí só tem um de cada.
        Index(
            "ux_reservas_uma_ativa_por_lead",
            "lead_id",
            unique=True,
            postgresql_where=literal_column("status = 'ativa'"),
        ),
        Index("ix_reservas_status_expira", "status", "expira_em"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    chassi: Mapped[str] = mapped_column(ForeignKey("unidades.chassi"))
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    conversa_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversas.id", ondelete="CASCADE"))
    # Invariante 3: reserva exige aprovação válida. As duas colunas são NOT NULL com FK.
    approval_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pedidos_de_aprovacao.id", ondelete="RESTRICT"), nullable=False
    )
    espelho_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("espelhos.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(10), default="ativa")
    renovacoes: Mapped[int] = mapped_column(Integer, default=0)
    criada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    liberada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    motivo_liberacao: Mapped[str | None] = mapped_column(String(30), default=None)


class Configuracao(Base):
    """S-12 §2 e ADR-014 — o que a Sol & Volt troca sem abrir terminal.

    `chave` é `String` no banco e `Chave` (Enum) no código: o banco guarda o texto, e quem
    fecha a lista é `app/configuracao.py`. Chave nova exige commit — sem isso, esta tabela
    é a porta lateral por onde alguém, um dia, cadastra `preco_do_seal` e contorna a
    invariante 1 por um formulário.

    **Todo valor é cifrado, inclusive os que não são segredo.** Um caminho só, sem uma
    coluna `e_segredo` para alguém esquecer de marcar na chave seguinte. O custo é não
    poder consultar por valor, e nada aqui precisa disso.
    """

    __tablename__ = "configuracoes"
    __table_args__ = (
        # A lista fechada é do banco, não só do `Enum` em Python: o `create_all` do banco de
        # teste cria o mesmo CHECK que a migration, senão a garantia só existiria em produção.
        CheckConstraint(
            "chave IN ('whatsapp_numero', 'evolution_url', 'evolution_instancia', "
            "'evolution_chave', 'llm_provedor', 'llm_modelo', 'llm_url', 'llm_chave', "
            "'llm_fallbacks')",
            name="ck_configuracoes_chave_conhecida",
        ),
    )

    chave: Mapped[str] = mapped_column(String(40), primary_key=True)
    valor_cifrado: Mapped[bytes] = mapped_column(LargeBinary)
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    atualizado_por: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("usuarios.id", ondelete="RESTRICT"), default=None
    )

    def __repr__(self) -> str:
        """Sem o valor, nem mascarado: metade desta tabela é credencial."""
        return f"<Configuracao {self.chave}>"


class TokenMigracao(Base):
    """S-06 §2 — o que amarra a conversa do WhatsApp à sessão do chat web.

    Uso único e 30 minutos. O alfabeto não tem `O`, `0`, `I` nem `1`: o cliente lê o
    código na tela e digita — ou deixa o `wa.me` preencher — e a confusão entre esses
    quatro é a que acontece de verdade.

    `telefone_hash_esperado` é quem pediu a migração. Não é usado para recusar (o cliente
    pode ter cadastrado um número e escrito de outro), mas é o que permite ver depois que
    um token foi resgatado por outra pessoa.
    """

    __tablename__ = "tokens_migracao"

    token: Mapped[str] = mapped_column(String(6), primary_key=True)
    conversa_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversas.id", ondelete="CASCADE"))
    telefone_hash_esperado: Mapped[str | None] = mapped_column(String(64), default=None)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    usado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    def __repr__(self) -> str:
        """Sem o token: ele é credencial de sessão enquanto não for usado."""
        return f"<TokenMigracao conversa={self.conversa_id} usado={self.usado_em is not None}>"
