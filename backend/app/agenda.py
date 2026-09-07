"""S-07 §2, §3 e §4 — a agenda real de Tarcísio e Jaqueline.

O que este módulo faz é **oferecer** horário. Quem garante que dois clientes não peguem
o mesmo é o índice de exclusão em `test_drives` (ADR-001): conferir em Python e gravar
depois deixa a janela aberta entre as duas operações, que é o mesmo erro que a S-05
proíbe na reserva de chassi. Por isso `agendar` grava e trata a recusa do banco, em vez
de confiar na conferência que ela mesma acabou de fazer.
"""

import uuid
from collections.abc import Iterator
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import FUSO, agora

# `_sem_acento` é o normalizador do turno, e é puro (só `re` e `unicodedata`). Uma segunda
# cópia dele aqui divergiria na primeira letra acentuada que alguém esquecesse.
from app.ia.verificacao import _sem_acento
from app.modelos import AgendaBloqueio, Conversa, TestDrive, Unidade, Vendedor

DURACAO = timedelta(minutes=45)
INTERVALO = timedelta(minutes=15)  # volta e conferência do carro
PASSO = DURACAO + INTERVALO
ANTECEDENCIA_MINIMA = timedelta(hours=2)
ANTECEDENCIA_MAXIMA = timedelta(days=14)
MAXIMO_DE_OPCOES = 3  # lista longa gera indecisão; uma só parece imposição
ALMOCO = (time(12), time(13))

# `weekday()`: 0 é segunda. Domingo não está no mapa — a loja fecha.
ABERTURA = {0: (9, 18), 1: (9, 18), 2: (9, 18), 3: (9, 18), 4: (9, 18), 5: (9, 13)}

JANELA_DE_CARGA = timedelta(days=7)


class HorarioIndisponivel(Exception):
    """O banco recusou, ou a reconferência achou o horário ocupado."""


class ChassiIndisponivel(Exception):
    """O carro saiu do estoque entre a oferta e o agendamento."""


def _em(dia: date, hora: time) -> datetime:
    return datetime.combine(dia, hora, tzinfo=FUSO)


def inicios_do_dia(dia: date) -> Iterator[datetime]:
    """A grade do dia: 45 min de test drive a cada hora, sem invadir o almoço."""
    faixa = ABERTURA.get(dia.weekday())
    if faixa is None:
        return
    abertura, fechamento = faixa
    almoco_inicio, almoco_fim = _em(dia, ALMOCO[0]), _em(dia, ALMOCO[1])
    inicio = _em(dia, time(abertura))
    limite = _em(dia, time(fechamento))
    while inicio + DURACAO <= limite:
        if not (inicio < almoco_fim and inicio + DURACAO > almoco_inicio):
            yield inicio
        inicio += PASSO


def _chassi_atendivel(sessao: Session, chassi: str, lead_id: uuid.UUID | None) -> bool:
    """S-07 §3.4 — disponível, ou já reservado para este mesmo lead."""
    unidade = sessao.get(Unidade, chassi)
    if unidade is None:
        return False
    if unidade.status == "disponivel":
        return True
    return unidade.status == "reservado" and unidade.reservado_para == lead_id


def _carga_por_vendedor(sessao: Session, referencia: datetime) -> dict[uuid.UUID, int]:
    """S-07 §4 — quem tem menos test drive nos próximos 7 dias leva o próximo."""
    linhas = sessao.execute(
        select(TestDrive.vendedor_id, func.count())
        .where(
            TestDrive.inicio >= referencia,
            TestDrive.inicio < referencia + JANELA_DE_CARGA,
            TestDrive.status != "cancelado",
        )
        .group_by(TestDrive.vendedor_id)
    )
    return {vendedor_id: total for vendedor_id, total in linhas}


def _opcoes(
    sessao: Session,
    *,
    chassi: str,
    lead_id: uuid.UUID | None,
    de: datetime,
    ate: datetime,
) -> Iterator[tuple[datetime, Vendedor]]:
    """Percorre a grade uma vez, com bloqueios e ocupações já em memória."""
    if not _chassi_atendivel(sessao, chassi, lead_id):
        return

    vendedores = list(
        sessao.scalars(select(Vendedor).where(Vendedor.ativo.is_(True)).order_by(Vendedor.nome))
    )
    if not vendedores:
        return

    bloqueios = list(
        sessao.scalars(
            select(AgendaBloqueio).where(AgendaBloqueio.fim > de, AgendaBloqueio.inicio <= ate)
        )
    )
    ocupacoes = list(
        sessao.scalars(
            select(TestDrive).where(
                TestDrive.fim > de,
                TestDrive.inicio <= ate,
                TestDrive.status != "cancelado",
            )
        )
    )
    carga = _carga_por_vendedor(sessao, de)

    dia = de.date()
    while dia <= ate.date():
        for inicio in inicios_do_dia(dia):
            fim = inicio + DURACAO
            if inicio < de or inicio > ate:
                continue
            if any(o.chassi == chassi and o.inicio < fim and o.fim > inicio for o in ocupacoes):
                continue
            livres = [
                vendedor
                for vendedor in vendedores
                if not any(
                    b.vendedor_id == vendedor.id and b.inicio < fim and b.fim > inicio
                    for b in bloqueios
                )
                and not any(
                    o.vendedor_id == vendedor.id and o.inicio < fim and o.fim > inicio
                    for o in ocupacoes
                )
            ]
            if livres:
                yield inicio, min(livres, key=lambda v: (carga.get(v.id, 0), v.nome))
        dia += timedelta(days=1)


def horarios_disponiveis(
    sessao: Session,
    *,
    chassi: str,
    lead_id: uuid.UUID | None = None,
    dia: date | None = None,
    referencia: datetime | None = None,
) -> list[tuple[datetime, Vendedor]]:
    """No máximo 3 opções, a primeira sendo a mais próxima disponível (§3)."""
    momento = referencia or agora()
    de = momento + ANTECEDENCIA_MINIMA
    ate = momento + ANTECEDENCIA_MAXIMA
    if dia is not None:
        de = max(de, _em(dia, time(0)))
        ate = min(ate, _em(dia, time(23, 59)))
        if de > ate:
            return []

    opcoes = []
    for opcao in _opcoes(sessao, chassi=chassi, lead_id=lead_id, de=de, ate=ate):
        opcoes.append(opcao)
        if len(opcoes) == MAXIMO_DE_OPCOES:
            break
    return opcoes


def agendar(
    sessao: Session,
    *,
    conversa: Conversa,
    lead_id: uuid.UUID,
    chassi: str,
    inicio: datetime,
    referencia: datetime | None = None,
) -> TestDrive:
    """S-07 §4 — reconfere, grava, e deixa o banco ter a última palavra."""
    momento = referencia or agora()
    inicio = inicio.astimezone(FUSO)
    if not _chassi_atendivel(sessao, chassi, lead_id):
        raise ChassiIndisponivel
    if not (momento + ANTECEDENCIA_MINIMA <= inicio <= momento + ANTECEDENCIA_MAXIMA):
        raise HorarioIndisponivel
    if inicio not in set(inicios_do_dia(inicio.date())):
        raise HorarioIndisponivel

    escolhido = next(
        (
            vendedor
            for horario, vendedor in _opcoes(
                sessao, chassi=chassi, lead_id=lead_id, de=inicio, ate=inicio
            )
            if horario == inicio
        ),
        None,
    )
    if escolhido is None:
        raise HorarioIndisponivel

    test_drive = TestDrive(
        conversa_id=conversa.id,
        lead_id=lead_id,
        chassi=chassi,
        vendedor_id=escolhido.id,
        inicio=inicio,
        fim=inicio + DURACAO,
    )
    sessao.add(test_drive)
    try:
        sessao.flush()
    except IntegrityError as erro:
        # Alguém agendou entre a reconferência e este INSERT. É para isso que o
        # índice de exclusão existe — a checagem acima é conveniência, não garantia.
        sessao.rollback()
        raise HorarioIndisponivel from erro

    conversa.etapa = "encerrada"
    conversa.desfecho = "test_drive_agendado"
    sessao.commit()
    return test_drive


# ── §7: remarcar e cancelar ──────────────────────────────────────────────────────

REMARCACOES_MAXIMAS = 2  # S-07 §7 — a terceira é conversa de gente, não de agenda


class RemarcacoesEsgotadas(Exception):
    """Duas remarcações já aconteceram. A terceira vai para humano (§7)."""


def remarcacoes_de(sessao: Session, conversa_id: uuid.UUID) -> int:
    """Quantas vezes esta conversa já mudou de horário.

    É a contagem dos `cancelado` da própria conversa, e não uma coluna: remarcar cria uma
    linha nova (é "cancelar e agendar de novo"), então um contador teria de ser copiado de
    uma linha para a seguinte — e é na cópia que ele para de bater.
    """
    total = sessao.scalar(
        select(func.count())
        .select_from(TestDrive)
        .where(TestDrive.conversa_id == conversa_id, TestDrive.status == "cancelado")
    )
    return total or 0


def cancelar(sessao: Session, test_drive: TestDrive) -> None:
    """S-07 §7 — cancelar test drive **não libera a reserva do chassi**.

    São coisas diferentes: a agenda é do vendedor, o chassi é do estoque. Liberar carro é
    decisão comercial, e ela tem um caminho próprio — o desfecho `desistiu` da §9 ou o
    prazo das 72 h da S-05. Uma linha a mais aqui, `unidade.status = 'disponivel'`, poria
    de volta no catálogo um carro que o cliente ainda vai buscar.

    O horário volta a ficar livre porque o `EXCLUDE` de `test_drives` ignora `cancelado` —
    isso é do banco, não desta função.
    """
    test_drive.status = "cancelado"
    sessao.flush()


def remarcar(
    sessao: Session,
    *,
    test_drive: TestDrive,
    inicio: datetime,
    limite: bool = True,
) -> TestDrive:
    """§7 — cancelar e agendar de novo, **na mesma transação**.

    Se o horário novo não serve, nada aconteceu: o cliente continua com o test drive que
    tinha, em vez de perder o antigo e não conseguir o novo. É por isso que o `rollback`
    está aqui e não em quem chama.

    `limite=False` é para quem já é gente: o teto de 2 da spec é da Aurora, e a terceira
    remarcação "vai para humano" — recusá-la ao vendedor que está com o cliente no
    telefone seria mandar o humano para o humano.
    """
    conversa = sessao.get(Conversa, test_drive.conversa_id)
    if conversa is None:  # pragma: no cover - FK garante
        raise HorarioIndisponivel
    if limite and remarcacoes_de(sessao, conversa.id) >= REMARCACOES_MAXIMAS:
        raise RemarcacoesEsgotadas

    cancelar(sessao, test_drive)
    try:
        return agendar(
            sessao,
            conversa=conversa,
            lead_id=test_drive.lead_id,
            chassi=test_drive.chassi,
            inicio=inicio,
        )
    except (ChassiIndisponivel, HorarioIndisponivel):
        # O `rollback` desfaz o cancelamento junto: o banco volta a ter o test drive
        # original, e a sessão relê o `status` dele no próximo acesso.
        sessao.rollback()
        raise


# S-07 §6 — "resposta afirmativa → confirmado". Palavra, não modelo: o cliente responde
# "sim" às 22h, e nessa hora a conversa pode estar em modo humano, com a Aurora fora. Uma
# lista fechada funciona nos dois modos e não custa uma chamada de LLM por "ok".
_AFIRMATIVAS = (
    "sim", "confirmo", "confirmado", "confirmar", "ok", "okay", "beleza", "combinado",
    "fechado", "isso", "claro", "certo", "positivo", "estarei", "vou", "to dentro",
    "tudo certo", "pode ser", "perfeito", "valeu",
)  # fmt: skip
# "nao vou" e "nao posso" contem "vou": a negacao vence a afirmativa, sempre. Falso
# positivo aqui marca como confirmada uma visita que o cliente acabou de desmarcar.
_NEGATIVAS = ("nao", "n vou", "desmarc", "cancel", "remarc", "outro dia", "outro horario")


def confirmou(texto: str) -> bool:
    """Se a fala do cliente é um "sim" a respeito do lembrete."""
    limpo = _sem_acento(texto)
    if any(negativa in limpo for negativa in _NEGATIVAS):
        return False
    return any(afirmativa in limpo for afirmativa in _AFIRMATIVAS)


def confirmar_se_o_cliente_respondeu(sessao: Session, conversa: Conversa, texto: str) -> bool:
    """S-07 §6 — o test drive vira `confirmado`, e o vendedor vê isso na tela.

    Só confirma o que foi lembrado: sem `lembrete_em`, uma mensagem qualquer com "ok"
    confirmaria uma visita sobre a qual o cliente nunca foi perguntado. Sem resposta,
    segue `agendado` — que é a outra metade da regra da spec.

    Não faz `commit`: quem chama está no meio de gravar a mensagem que chegou, e as duas
    coisas são o mesmo fato.
    """
    if not confirmou(texto):
        return False
    test_drive = sessao.scalars(
        select(TestDrive)
        .where(
            TestDrive.conversa_id == conversa.id,
            TestDrive.status == "agendado",
            TestDrive.lembrete_em.is_not(None),
            TestDrive.inicio > agora(),
        )
        .order_by(TestDrive.inicio)
    ).first()
    if test_drive is None:
        return False
    test_drive.status = "confirmado"
    test_drive.confirmado_em = agora()
    return True
