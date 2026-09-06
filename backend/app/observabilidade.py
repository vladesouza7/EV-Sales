"""S-08 — trilha de auditoria, custo faturado e o teto que corta em código.

Escrita **antes** do agente existir, por decisão de ordem do ADR-006: depurar por que a
Aurora escolheu uma tool sem ver o retorno daquela chamada é adivinhação, e instrumentação
escrita depois é instrumentação escrita para confirmar o que eu já acho que o sistema faz.

Duas leituras, uma origem (ADR-006): a tela do Raí lê a trilha como transcrição, eu leio a
mesma trilha como trace. E a PII é mascarada **aqui**, na montagem do span — se eu trocar
de ferramenta de observabilidade amanhã, a proteção vai junto (ADR-007, invariante 5).
"""

import logging
import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.pii import redigir
from app.db import agora
from app.modelos import Incidente, Trilha

logger = logging.getLogger(__name__)

# Um real tem 1.000.000 de micro-reais; um centavo, 10.000. Ver a nota em `Trilha`.
MICRO_REAIS_POR_REAL = 1_000_000

TETO_MICRO_REAIS = 900 * MICRO_REAIS_POR_REAL
ALERTA_MICRO_REAIS = 720 * MICRO_REAIS_POR_REAL
CUSTO_ALTO_MICRO_REAIS = 45 * 10_000  # R$ 0,45 por conversa

# S-08 §3. Honesta: não inventa prazo, não culpa o cliente e não finge estar pensando.
MENSAGEM_NO_TETO = (
    "Oi! No momento nosso atendimento automático está indisponível. Já avisei o Tarcísio "
    "e ele te responde em seguida — pode deixar sua pergunta aqui."
)

# S-08 §6. O tipo do incidente decide a gravidade: quem registra não escolhe o quanto
# aquilo importa, senão o mesmo incidente sai crítico num lugar e baixo no outro.
GRAVIDADE: dict[str, str] = {
    "numero_divergente": "alta",
    "chassi_divergente": "critica",
    "reserva_perdida": "informativa",
    "injection_suspeita": "alta",
    "evolution_desconectada": "critica",
    "teto_atingido": "critica",
    "custo_alto": "baixa",
    # O alerta de 80% da §3. Não está na tabela da §6 porque lá só entrou o que já
    # aconteceu; este é o aviso de que vai acontecer, e o Raí quer os dois.
    "custo_perto_do_teto": "alta",
}


def _mascarar(valor: object) -> object:
    """A camada que monta o span, e é o único lugar onde isso acontece (ADR-006).

    Nome completo não é reconhecível por padrão — quem registra um nome passa o
    `mascarar_nome` antes. O que esta função garante é o resto: telefone, CPF, e-mail,
    placa e cartão não entram na trilha em claro, nem por descuido de quem instrumenta.
    """
    if isinstance(valor, str):
        return redigir(valor)
    if isinstance(valor, dict):
        return {chave: _mascarar(item) for chave, item in valor.items()}
    if isinstance(valor, list):
        return [_mascarar(item) for item in valor]
    return valor


def registrar(
    sessao: Session,
    conversa_id: uuid.UUID | None,
    tipo: str,
    nome: str,
    *,
    dados: dict[str, object] | None = None,
    duracao_ms: int | None = None,
    custo_micro_reais: int = 0,
) -> Trilha:
    linha = Trilha(
        conversa_id=conversa_id,
        tipo=tipo,
        nome=nome,
        dados=_mascarar(dados or {}),
        duracao_ms=duracao_ms,
        custo_micro_reais=custo_micro_reais,
    )
    sessao.add(linha)
    sessao.commit()
    return linha


def registrar_incidente(
    sessao: Session,
    conversa_id: uuid.UUID | None,
    tipo: str,
    dados: dict[str, object] | None = None,
) -> Incidente:
    incidente = Incidente(
        conversa_id=conversa_id,
        tipo=tipo,
        gravidade=GRAVIDADE[tipo],
        dados=_mascarar(dados or {}),
    )
    sessao.add(incidente)
    sessao.commit()
    # ponytail: o alerta de gravidade crítica sai por log até a Evolution API existir
    # (S-06). O canal do Raí é o WhatsApp, e é lá que ele precisa chegar.
    logger.warning("incidente %s (%s) conversa=%s", tipo, incidente.gravidade, conversa_id)
    return incidente


def _ja_houve(
    sessao: Session,
    tipo: str,
    *,
    desde: datetime | None = None,
    conversa_id: uuid.UUID | None = None,
) -> bool:
    consulta = select(Incidente.id).where(Incidente.tipo == tipo)
    if desde is not None:
        consulta = consulta.where(Incidente.criado_em >= desde)
    if conversa_id is not None:
        consulta = consulta.where(Incidente.conversa_id == conversa_id)
    return sessao.scalars(consulta.limit(1)).one_or_none() is not None


def gasto_do_mes(sessao: Session, quando: datetime | None = None) -> int:
    """Soma dos turnos do mês corrente, em micro-reais.

    ponytail: `SUM` sobre a trilha, não contador em Redis. São ~280 conversas por mês
    numa loja — o índice de `criado_em` resolve, e o número somado é o mesmo que a tela
    de custo mostra, sem um segundo lugar onde ele possa divergir. Vira contador em
    Redis quando a soma aparecer no p95, junto com o rate limit da `limite.py`.
    """
    referencia = quando or agora()
    inicio = referencia.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    total = sessao.scalar(
        select(func.coalesce(func.sum(Trilha.custo_micro_reais), 0)).where(
            Trilha.criado_em >= inicio
        )
    )
    return int(total or 0)


def custo_da_conversa(sessao: Session, conversa_id: uuid.UUID) -> int:
    total = sessao.scalar(
        select(func.coalesce(func.sum(Trilha.custo_micro_reais), 0)).where(
            Trilha.conversa_id == conversa_id
        )
    )
    return int(total or 0)


def pode_chamar_llm(sessao: Session) -> bool:
    """S-08 §3 — a checagem que roda antes de **cada** chamada ao modelo.

    Devolve `bool` e não a `Decisao` de dois valores que a spec desenha: um enum de dois
    estados é um `bool` com cerimônia. O alerta de 80% não é um terceiro estado — é um
    efeito colateral do caminho que segue.
    """
    gasto = gasto_do_mes(sessao)
    inicio_do_dia = agora().replace(hour=0, minute=0, second=0, microsecond=0)

    if gasto >= TETO_MICRO_REAIS:
        if not _ja_houve(sessao, "teto_atingido", desde=inicio_do_dia):
            registrar_incidente(sessao, None, "teto_atingido", {"gasto_micro_reais": gasto})
        return False

    if gasto >= ALERTA_MICRO_REAIS and not _ja_houve(
        sessao, "custo_perto_do_teto", desde=inicio_do_dia
    ):
        registrar_incidente(sessao, None, "custo_perto_do_teto", {"gasto_micro_reais": gasto})
    return True


def marcar_se_conversa_cara(sessao: Session, conversa_id: uuid.UUID) -> None:
    """S-08 §3 — marca e segue. Cliente no meio da compra não é lugar de economizar."""
    custo = custo_da_conversa(sessao, conversa_id)
    if custo <= CUSTO_ALTO_MICRO_REAIS:
        return
    # Uma marca por conversa: o segundo turno caro não é um segundo incidente.
    if _ja_houve(sessao, "custo_alto", conversa_id=conversa_id):
        return
    registrar_incidente(sessao, conversa_id, "custo_alto", {"custo_micro_reais": custo})
