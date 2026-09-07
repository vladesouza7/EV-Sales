"""S-07 — agendamento de test drive, e o desfecho que fecha a jornada.

Recorte implementado: agenda real, atribuição de vendedor, gravação com o índice de
exclusão decidindo, e o **registro de desfecho da §9** — o único ponto em que o EV-Sales
sabe que a venda aconteceu. **Fora daqui, ainda:** lembrete de 24h (§6) e dossiê do
vendedor (§8), que depende das objeções da `buscar_conhecimento`, tool que não existe.

O desfecho é a fronteira do ADR-011 sendo respeitada: o sistema registra **que** vendeu, e
nada mais. Nota fiscal, financiamento e documentação acontecem na loja, e nem por
integração com o DMS — são 11 toques por mês.
"""

import logging
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.agenda import ChassiIndisponivel, HorarioIndisponivel, agendar, horarios_disponiveis
from app.autenticacao import exige_perfil
from app.core.dinheiro import formatar
from app.db import FUSO, agora, obter_sessao
from app.leads import LeadEntrada, abrir_conversa
from app.modelos import Lead, Reserva, TestDrive, Unidade, Usuario, Vendedor
from app.observabilidade import registrar

logger = logging.getLogger(__name__)

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
ENDERECO = "Sol & Volt — Av. Epitácio Pessoa, 2.140, Tambaú, João Pessoa"

router = APIRouter()
BancoDeDados = Annotated[Session, Depends(obter_sessao)]
Equipe = Annotated[Usuario, exige_perfil("dono", "gerente", "vendedor")]

# S-07 §9, a tabela da spec virando código — e é só isto que decide o efeito de um toque.
# `None` significa "não mexe": `vai_pensar` e `nao_compareceu` mantêm a reserva até as 72h,
# e é a rotina da S-05 §4 que a libera se ninguém voltar.
EFEITOS: dict[str, tuple[str | None, str | None, str]] = {
    "vendeu": ("vendido", "concluida", "ganho"),
    "vai_pensar": (None, None, "em_negociacao"),
    "desistiu": ("disponivel", "liberada", "perdido"),
    "nao_compareceu": (None, None, "a_recontatar"),
}

# §9, "contra o esquecimento": a primeira cobrança 2 h depois do horário, a segunda 24 h
# depois. Da segunda em diante o item fica visível para a gerente, que vê todos.
PRIMEIRA_COBRANCA = timedelta(hours=2)
SEGUNDA_COBRANCA = timedelta(hours=24)


class TestDriveEntrada(LeadEntrada):
    """Herda nome e telefone com a mesma validação e cifragem da landing."""

    chassi: str = Field(min_length=17, max_length=17)
    inicio: datetime
    origem: str = Field("test_drive", max_length=32)

    @field_validator("inicio")
    @classmethod
    def _com_fuso(cls, bruto: datetime) -> datetime:
        # A Paraíba não tem horário de verão, mas o navegador pode mandar sem fuso.
        return bruto.replace(tzinfo=FUSO) if bruto.tzinfo is None else bruto.astimezone(FUSO)


@router.get("/test-drive", include_in_schema=False)
def pagina_do_test_drive() -> FileResponse:
    return FileResponse(FRONTEND / "test-drive.html")


@router.get("/api/agenda")
def agenda(chassi: str, sessao: BancoDeDados, dia: date | None = None) -> list[dict[str, str]]:
    """No máximo 3 horários (S-07 §3), com o vendedor que acompanharia cada um."""
    return [
        {
            "inicio": inicio.isoformat(),
            "rotulo": _rotulo(inicio),
            "vendedor": vendedor.nome,
        }
        for inicio, vendedor in horarios_disponiveis(sessao, chassi=chassi, dia=dia)
    ]


@router.post("/api/test-drives", status_code=201)
def marcar(
    entrada: TestDriveEntrada, requisicao: Request, sessao: BancoDeDados
) -> dict[str, object]:
    conversa = abrir_conversa(sessao, entrada, _ip(requisicao))
    try:
        marcado = agendar(
            sessao,
            conversa=conversa,
            lead_id=conversa.lead_id,
            chassi=entrada.chassi,
            inicio=entrada.inicio,
        )
    except ChassiIndisponivel:
        raise HTTPException(
            409, detail={"mensagem": "Esse carro saiu do estoque. Escolhe outro?"}
        ) from None
    except HorarioIndisponivel:
        raise HTTPException(
            409, detail={"mensagem": "Esse horário acabou de ser tomado. Vê os próximos?"}
        ) from None

    unidade = sessao.get(Unidade, marcado.chassi)
    vendedor = sessao.get(Vendedor, marcado.vendedor_id)
    assert unidade is not None and vendedor is not None  # FK garante
    return {
        "test_drive_id": str(marcado.id),
        "quando": _rotulo(marcado.inicio.astimezone(FUSO)),
        "carro": f"{unidade.marca} {unidade.modelo} {unidade.versao} · {unidade.cor.lower()}",
        "vendedor": vendedor.nome,
        "endereco": ENDERECO,
        # A CNH é conferida no balcão: o sistema não coleta documento (ADR-007).
        "lembrete": "Leve sua CNH. Se precisar remarcar, é só chamar a Aurora.",
    }


DIAS = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")
MESES = (
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)  # fmt: skip


def _rotulo(quando: datetime) -> str:
    """"Quinta, 5 de setembro, às 14h" — o formato da confirmação da S-07 §5."""
    hora = f"{quando.hour}h" if quando.minute == 0 else f"{quando.hour}h{quando.minute:02d}"
    return (
        f"{DIAS[quando.weekday()].capitalize()}, {quando.day} de "
        f"{MESES[quando.month - 1]}, às {hora}"
    )


# ── §9: o registro de desfecho ───────────────────────────────────────────────────


class DesfechoEntrada(BaseModel):
    compareceu: bool
    desfecho: str

    @field_validator("desfecho")
    @classmethod
    def _conhecido(cls, bruto: str) -> str:
        if bruto not in EFEITOS:
            raise ValueError(f"desfecho deve ser um de {', '.join(EFEITOS)}")
        return bruto


@router.get("/desfecho", include_in_schema=False)
def pagina_do_desfecho() -> FileResponse:
    return FileResponse(FRONTEND / "desfecho.html")


def pendentes(sessao: Session, quem: Usuario) -> list[TestDrive]:
    """O que já aconteceu e continua sem desfecho.

    O recorte é o da S-11 §5: o vendedor vê os dele, a Neuza e o Raí veem todos — que é
    como "o item entra na tela da Neuza" (§9) acontece sem uma segunda tela.
    """
    consulta = (
        select(TestDrive)
        .where(
            TestDrive.desfecho.is_(None),
            TestDrive.status != "cancelado",
            TestDrive.inicio <= agora() - PRIMEIRA_COBRANCA,
        )
        .order_by(TestDrive.inicio)
    )
    if quem.perfil == "vendedor":
        consulta = consulta.where(TestDrive.vendedor_id == quem.vendedor_id)
    return list(sessao.scalars(consulta))


@router.get("/api/desfechos/pendentes")
def fila_de_desfechos(sessao: BancoDeDados, quem: Equipe) -> list[dict[str, object]]:
    return [
        {
            "test_drive_id": str(td.id),
            "quando": _rotulo(td.inicio.astimezone(FUSO)),
            "carro": _carro(sessao, td.chassi),
            "cliente": _nome_do_cliente(sessao, td.lead_id),
            "cobrancas": td.cobrancas,
        }
        for td in pendentes(sessao, quem)
    ]


@router.post("/api/test-drives/{test_drive_id}/desfecho")
def marcar_desfecho(
    test_drive_id: uuid.UUID,
    entrada: DesfechoEntrada,
    requisicao: Request,
    sessao: BancoDeDados,
    quem: Equipe,
) -> dict[str, object]:
    """S-07 §9 — um toque, e é o único caminho para `unidades.status = 'vendido'`.

    Nenhuma rotina chega aqui: o efeito irreversível do projeto depende de gente
    autenticada, e o CHECK do banco recusa desfecho sem autor e sem hora.
    """
    test_drive = _do_meu_recorte(sessao, quem, test_drive_id)
    if test_drive.desfecho is not None:
        raise HTTPException(
            409,
            detail={
                "mensagem": "Esse test drive já tem desfecho registrado.",
                "desfecho": test_drive.desfecho,
            },
        )
    if entrada.compareceu == (entrada.desfecho == "nao_compareceu"):
        # "compareceu e não compareceu" é contradição, não preferência: a tela manda os
        # dois campos, e aceitar a contradição gravaria um histórico que ninguém entende.
        raise HTTPException(
            422,
            detail={"mensagem": "Quem não compareceu tem desfecho 'nao_compareceu', e só."},
        )
    return registrar_desfecho(sessao, test_drive, entrada.desfecho, quem, _ip(requisicao))


def registrar_desfecho(
    sessao: Session, test_drive: TestDrive, desfecho: str, quem: Usuario, ip: str
) -> dict[str, object]:
    """Os três efeitos da tabela da §9, numa transação: unidade, reserva e lead."""
    status_unidade, status_reserva, situacao = EFEITOS[desfecho]
    agora_ = agora()

    if status_unidade is not None:
        # `UPDATE … WHERE`, como a reserva da S-05: sem `SELECT` para conferir antes.
        # `vendido` pode vir de `reservado` (o caminho normal) ou de `disponivel` — a §3.4
        # permite test drive de carro não reservado. `disponivel` só volta de `reservado`,
        # senão um segundo toque devolveria ao pátio um carro já vendido.
        origens = ["reservado", "disponivel"] if status_unidade == "vendido" else ["reservado"]
        sessao.execute(
            update(Unidade)
            .where(Unidade.chassi == test_drive.chassi, Unidade.status.in_(origens))
            .values(
                status=status_unidade,
                reservado_para=None if status_unidade == "disponivel" else test_drive.lead_id,
                reservado_em=None if status_unidade == "disponivel" else agora_,
            )
        )

    if status_reserva is not None:
        reserva = sessao.scalars(
            select(Reserva).where(
                Reserva.chassi == test_drive.chassi, Reserva.status == "ativa"
            )
        ).one_or_none()
        if reserva is not None:
            reserva.status = status_reserva
            if status_reserva == "liberada":
                reserva.liberada_em = agora_
                reserva.motivo_liberacao = "desistencia"

    lead = sessao.get(Lead, test_drive.lead_id)
    if lead is not None:
        lead.situacao = situacao

    test_drive.compareceu = desfecho != "nao_compareceu"
    test_drive.desfecho = desfecho
    test_drive.desfecho_em = agora_
    test_drive.desfecho_por = quem.id
    test_drive.status = "realizado" if test_drive.compareceu else "nao_compareceu"
    sessao.commit()

    # §9: "a auditoria grava quem marcou, quando e de qual IP". O `quando` é o próprio
    # `criado_em` da trilha, e o nome do vendedor não entra — o id basta e não é PII.
    registrar(
        sessao,
        test_drive.conversa_id,
        "evento",
        "desfecho_registrado",
        dados={
            "test_drive_id": str(test_drive.id),
            "desfecho": desfecho,
            "chassi": test_drive.chassi,
            "por": str(quem.id),
            "perfil": quem.perfil,
            "ip": ip,
        },
    )
    return {"desfecho": desfecho, "situacao_do_lead": situacao}


def cobrar_desfechos(sessao: Session) -> int:
    """§9, "contra o esquecimento" — as duas cobranças, e nada além delas.

    A rotina **não** registra desfecho e nunca registrará: ela lembra. Marcar `vendido`
    por decurso de prazo é o cenário que a spec proíbe por escrito.
    """
    from app.whatsapp import avisar_equipe, quem_recebe

    agora_ = agora()
    cobrados = 0
    for test_drive in sessao.scalars(
        select(TestDrive).where(
            TestDrive.desfecho.is_(None),
            TestDrive.status != "cancelado",
            TestDrive.cobrancas < 2,
        )
    ):
        prazo = PRIMEIRA_COBRANCA if test_drive.cobrancas == 0 else SEGUNDA_COBRANCA
        if test_drive.inicio > agora_ - prazo:
            continue
        vendedor = sessao.get(Vendedor, test_drive.vendedor_id)
        texto = (
            f"EV-Sales · e o test drive de {_rotulo(test_drive.inicio.astimezone(FUSO))}? "
            f"{_carro(sessao, test_drive.chassi)} — registra o desfecho em /desfecho."
        )
        alvo: list[Usuario | Vendedor] = [vendedor] if vendedor else []
        if test_drive.cobrancas == 1:
            # A segunda cobrança também vai para a gerente: §9 põe o item na tela dela, e
            # o aviso é o que faz alguém abrir a tela.
            alvo += list(quem_recebe(sessao, "gerente"))
        avisar_equipe(sessao, alvo, texto)
        test_drive.cobrancas += 1
        cobrados += 1
    sessao.commit()
    return cobrados


def _do_meu_recorte(sessao: Session, quem: Usuario, test_drive_id: uuid.UUID) -> TestDrive:
    """S-11 §5 — 404 e não 403: quem não alcança um recurso não descobre que ele existe."""
    test_drive = sessao.get(TestDrive, test_drive_id)
    if test_drive is None or (
        quem.perfil == "vendedor" and test_drive.vendedor_id != quem.vendedor_id
    ):
        raise HTTPException(404, detail={"mensagem": "Test drive não encontrado."})
    return test_drive


def _carro(sessao: Session, chassi: str) -> str:
    """Preço relido do Postgres na hora de montar (§8), nunca guardado na tela."""
    unidade = sessao.get(Unidade, chassi)
    if unidade is None:  # pragma: no cover - FK garante
        return chassi
    return (
        f"{unidade.marca} {unidade.modelo} {unidade.cor.lower()} …{chassi[-4:]} · "
        f"{formatar(unidade.preco_centavos)}"
    )


def _nome_do_cliente(sessao: Session, lead_id: uuid.UUID) -> str:
    """Mascarado: a tela de desfecho é uma lista, e lista não precisa de nome inteiro
    (S-09 §3). O nome completo é do dossiê da §8, que é do lead do próprio vendedor."""
    lead = sessao.get(Lead, lead_id)
    return lead.nome_mascarado() if lead is not None else "?"


def _ip(requisicao: Request) -> str:
    return requisicao.client.host if requisicao.client else "desconhecido"
