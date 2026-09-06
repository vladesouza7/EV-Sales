"""S-07 — agendamento de test drive pela página pública.

Recorte implementado: agenda real, atribuição de vendedor e gravação com o índice de
exclusão decidindo. **Fora daqui, ainda:** lembrete de 24h (§6), dossiê do vendedor
(§8) e registro de desfecho (§9) — dependem do WhatsApp da S-06 e da tela da S-04.
"""

from datetime import date, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import Field, field_validator
from sqlalchemy.orm import Session

from app.agenda import ChassiIndisponivel, HorarioIndisponivel, agendar, horarios_disponiveis
from app.db import FUSO, obter_sessao
from app.leads import LeadEntrada, abrir_conversa
from app.modelos import Unidade, Vendedor

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
ENDERECO = "Sol & Volt — Av. Epitácio Pessoa, 2.140, Tambaú, João Pessoa"

router = APIRouter()
BancoDeDados = Annotated[Session, Depends(obter_sessao)]


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
    ip = requisicao.client.host if requisicao.client else "desconhecido"
    conversa = abrir_conversa(sessao, entrada, ip)
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
