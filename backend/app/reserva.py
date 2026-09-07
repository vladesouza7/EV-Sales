"""S-05 — tirar um chassi do estoque para um cliente, e nunca para dois.

> "Carro eu tenho um de cada. Se esse negócio prometer o mesmo Seal branco pra duas
> pessoas, alguém vai ter que ligar pra uma delas e desmarcar. E esse alguém sou eu."

**A corrida é decidida pelo Postgres, numa instrução.** Não há `SELECT` para conferir se
está disponível: conferir e depois gravar cria exatamente a janela de milissegundos em que
os dois clientes ganham. O `UPDATE … WHERE status = 'disponivel'` devolve zero linhas para
quem chegou depois, e zero linhas é a derrota — não uma exceção, não um erro.

O CLAUDE.md registra que trocar esse UPDATE por `SELECT` + `UPDATE` "para melhorar a
mensagem de erro" já foi tentado neste repositório. A mensagem melhor está aqui embaixo,
no retorno estruturado, e a instrução continua sendo uma só.
"""

import logging
import uuid
from datetime import timedelta

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.db import agora
from app.modelos import Conversa, Espelho, PedidoDeAprovacao, Reserva, Unidade, Vendedor
from app.observabilidade import registrar, registrar_incidente

logger = logging.getLogger(__name__)

PRAZO = timedelta(hours=72)
RENOVACOES_MAXIMAS = 2

# S-05 §1 — a instrução que decide a corrida. Está aqui, isolada e nomeada, para que
# qualquer mudança nela apareça sozinha no diff.
_RESERVAR = text(
    """
    UPDATE unidades
       SET status         = 'reservado',
           reservado_para = :lead_id,
           reservado_em   = :agora
     WHERE chassi = :chassi
       AND status = 'disponivel'
    RETURNING chassi
    """
)


def reservar_chassi(
    sessao: Session, conversa: Conversa, chassi: str, approval_id: str
) -> dict[str, object]:
    """A tool da etapa `reserva`. Devolve o motivo em vez de levantar exceção.

    `approval_id` vem do modelo, e a tool **não confia nele**: é validado contra o banco,
    e contra esta conversa. Sem a segunda checagem, um id de aprovação de outra conversa
    reservaria um carro para o cliente errado.
    """
    pedido = sessao.get(PedidoDeAprovacao, uuid.UUID(approval_id)) if approval_id else None
    if pedido is None or pedido.conversa_id != conversa.id or pedido.status != "aprovado":
        return {"resultado": "aprovacao_invalida"}

    espelho = sessao.scalars(
        select(Espelho).where(Espelho.approval_id == pedido.id)
    ).one_or_none()
    if espelho is None:
        registrar_incidente(sessao, conversa.id, "numero_divergente", {"falta": "espelho"})
        return {"resultado": "sem_espelho"}
    if espelho.valido_ate <= agora():
        return {"resultado": "aprovacao_invalida"}

    if espelho.chassi != chassi:
        # Incidente grave: o modelo pediu um carro diferente do que foi aprovado. Nenhuma
        # unidade é tocada.
        registrar_incidente(
            sessao,
            conversa.id,
            "chassi_divergente",
            {"pedido_no_espelho": espelho.chassi, "pedido_na_tool": chassi},
        )
        return {"resultado": "chassi_divergente"}

    ja_tem = sessao.scalars(
        select(Reserva).where(Reserva.lead_id == conversa.lead_id, Reserva.status == "ativa")
    ).first()
    if ja_tem is not None:
        return {"resultado": "ja_tem_reserva", "chassi": ja_tem.chassi}

    agora_ = agora()
    # ── a corrida acaba aqui ──────────────────────────────────────────────────────
    vencedor = sessao.execute(
        _RESERVAR, {"lead_id": conversa.lead_id, "agora": agora_, "chassi": chassi}
    ).first()

    if vencedor is None:
        # Zero linhas: outro cliente chegou primeiro. Não é erro — é o resultado normal
        # de dois clientes querendo o mesmo carro.
        sessao.rollback()
        pedido.status = "expirado"
        conversa.etapa = "recomendacao"
        conversa.chassi_em_foco = None
        sessao.commit()
        registrar(sessao, conversa.id, "evento", "reserva_perdida", dados={"chassi": chassi})
        return {"resultado": "indisponivel", "chassi": chassi}

    reserva = Reserva(
        chassi=chassi,
        lead_id=conversa.lead_id,
        conversa_id=conversa.id,
        approval_id=pedido.id,
        espelho_id=espelho.id,
        criada_em=agora_,
        expira_em=agora_ + PRAZO,
    )
    sessao.add(reserva)
    conversa.etapa = "test_drive"
    conversa.chassi_em_foco = chassi
    # Tudo na mesma transação: se o INSERT falhar, o UPDATE desfaz e a unidade nunca
    # chegou a sair do estoque.
    sessao.commit()

    registrar(
        sessao,
        conversa.id,
        "evento",
        "reservado",
        dados={"chassi": chassi, "reserva_id": str(reserva.id), "espelho": espelho.numero},
    )
    return {
        "resultado": "reservado",
        "chassi": chassi,
        "horas": int(PRAZO.total_seconds() // 3600),
    }


def liberar_vencidas(sessao: Session) -> list[str]:
    """S-05 §4 — 72 horas e o carro volta para o pátio. Roda a cada 5 minutos.

    O `WHERE status = 'reservado'` na unidade não é redundante: se o vendedor já marcou o
    desfecho `vendeu`, a unidade está em `vendido`, e devolvê-la a `disponivel` colocaria
    à venda um carro que saiu da loja — catálogo mentindo, que é o que o ADR-001 existe
    para impedir.
    """
    vencidas = list(
        sessao.scalars(
            select(Reserva).where(Reserva.status == "ativa", Reserva.expira_em <= agora())
        )
    )
    agora_ = agora()
    for reserva in vencidas:
        sessao.execute(
            update(Unidade)
            .where(Unidade.chassi == reserva.chassi, Unidade.status == "reservado")
            .values(status="disponivel", reservado_para=None, reservado_em=None)
        )
        reserva.status = "liberada"
        reserva.liberada_em = agora_
        reserva.motivo_liberacao = "prazo"
    sessao.commit()

    for reserva in vencidas:
        # Reserva que vence sem desfecho é reserva órfã: alguém prometeu um carro e
        # ninguém registrou o que aconteceu. Entra na contagem semanal (S-08 §6).
        registrar_incidente(
            sessao,
            reserva.conversa_id,
            "reserva_perdida",
            {"chassi": reserva.chassi, "reserva_id": str(reserva.id)},
        )
        logger.warning("reserva vencida liberada: chassi %s", reserva.chassi)

    chassis = [r.chassi for r in vencidas]
    _avisar_a_equipe_de_vendas(sessao, chassis)
    return chassis


def _avisar_a_equipe_de_vendas(sessao: Session, chassis: list[str]) -> int:
    """S-05 §4 — "ao liberar, notifica o vendedor responsável".

    Uma mensagem com a lista, não uma por chassi: a rotina roda a cada 5 minutos e três
    reservas vencendo juntas não são três avisos.

    O chassi vai por extenso porque não é PII — é do carro, não da pessoa (S-09 §1).

    ponytail: avisa os vendedores **ativos**, e não "o responsável", porque `reservas` não
    guarda vendedor: quem atende é quem estiver na agenda do dia (S-07). Vira aviso
    dirigido quando a reserva guardar o vendedor, e isso é migration em `reservas` —
    revisão humana obrigatória (CLAUDE.md).
    """
    if not chassis:
        return 0
    # Import atrasado: `whatsapp` chega a este módulo pelo registro de tools do turno, e o
    # ciclo no topo do arquivo derruba o app no import.
    from app.whatsapp import avisar_equipe

    vendedores = sessao.scalars(
        select(Vendedor).where(Vendedor.ativo.is_(True)).order_by(Vendedor.nome)
    ).all()
    texto = (
        "EV-Sales · reserva vencida sem desfecho: "
        + ", ".join(chassis)
        + " — de volta ao pátio, e já no catálogo."
    )
    return avisar_equipe(sessao, vendedores, texto)


def renovar(sessao: Session, reserva_id: uuid.UUID) -> dict[str, object]:
    """S-05 §4 — +72h, no máximo duas vezes, e só por gente autenticada.

    O teto de duas renovações é do banco (`ck_reservas_renovacoes`); aqui ele vira uma
    resposta legível em vez de um erro de constraint na cara do vendedor.
    """
    reserva = sessao.get(Reserva, reserva_id)
    if reserva is None or reserva.status != "ativa":
        return {"resultado": "reserva_inativa"}
    if reserva.renovacoes >= RENOVACOES_MAXIMAS:
        return {"resultado": "limite_de_renovacoes", "renovacoes": reserva.renovacoes}

    reserva.renovacoes += 1
    reserva.expira_em = reserva.expira_em + PRAZO
    sessao.commit()
    registrar(
        sessao,
        reserva.conversa_id,
        "evento",
        "reserva_renovada",
        dados={"chassi": reserva.chassi, "renovacoes": reserva.renovacoes},
    )
    return {
        "resultado": "renovada",
        "expira_em": reserva.expira_em.isoformat(),
        "renovacoes": reserva.renovacoes,
    }
