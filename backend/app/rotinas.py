"""O que o sistema faz sozinho, sem ninguém clicar.

Duas coisas, e as duas já existiam escritas e sem quem as chamasse:

1. **Liberar reserva vencida** ([S-05 §4](../../docs/spec/S-05-reserva-de-chassi.md)) — 72 h
   e o carro volta ao pátio. Sem isto, `liberar_vencidas` é código morto e um carro fica
   `reservado` para sempre: catálogo mentindo, que é o que o ADR-001 existe para impedir.
2. **Escalar aprovação parada** ([S-04 §3](../../docs/spec/S-04-fila-de-aprovacao.md)) — 15
   minutos sem a Neuza decidir e a mesma notificação vai para o Raí.

ponytail: um `asyncio.Task` no processo da API, não Redis com worker. É uma loja, um
servidor (S-10), e a operação é idempotente — `liberar_vencidas` só age sobre unidade que
ainda está `reservado`, e o escalonamento carimba `escalado_em`. Com mais de uma réplica as
duas rodariam, e o resultado continuaria certo. Vira consumidor de fila quando existir fila.
"""

import asyncio
import logging
from contextlib import suppress

from sqlalchemy.orm import Session

from app.aprovacao import escalar_pendentes
from app.db import Sessao
from app.reserva import liberar_vencidas

logger = logging.getLogger(__name__)

INTERVALO_S = 300  # S-05 §4 — "rotina a cada 5 min"


def ciclo(sessao: Session) -> tuple[int, int]:
    """Uma passada. Separada do laço porque é o que a suíte consegue chamar — testar
    `while True: await sleep(300)` seria testar o `asyncio`, não a regra."""
    liberadas = liberar_vencidas(sessao)
    escalados = escalar_pendentes(sessao)
    return len(liberadas), escalados


async def _girar() -> None:
    while True:
        await asyncio.sleep(INTERVALO_S)
        try:
            with Sessao() as sessao:
                liberadas, escalados = ciclo(sessao)
            if liberadas or escalados:
                logger.info(
                    "rotina: %d reserva(s) liberada(s), %d aprovação(ões) escalada(s)",
                    liberadas,
                    escalados,
                )
        except Exception:
            # Uma falha não pode matar o laço: sem ele, reserva vencida nunca mais é
            # liberada, e ninguém percebe até um cliente perguntar pelo carro.
            logger.exception("rotina periódica falhou; segue no próximo ciclo")


def começar() -> asyncio.Task[None]:
    return asyncio.create_task(_girar())


async def parar(tarefa: asyncio.Task[None]) -> None:
    tarefa.cancel()
    with suppress(asyncio.CancelledError):
        await tarefa
