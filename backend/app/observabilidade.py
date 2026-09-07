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

from app.core import dinheiro
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
    # ADR-012: o provedor configurado não devolve custo faturado. Não é anomalia, é
    # configuração — mas precisa aparecer, senão o teto para de proteger em silêncio.
    "custo_nao_faturado": "baixa",
    # O alerta de 80% da §3. Não está na tabela da §6 porque lá só entrou o que já
    # aconteceu; este é o aviso de que vai acontecer, e o Raí quer os dois.
    "custo_perto_do_teto": "alta",
}

# S-08 §6, coluna "Ação": "alerta imediato" é exatamente a gravidade crítica, então a
# regra é derivada dela e não de uma segunda lista — tipo crítico novo já entra avisando.
# O `custo_perto_do_teto` é a exceção nomeada: é `alta`, e a §3 manda o WhatsApp de todo
# jeito, porque o aviso de 80% só serve antes de o teto cortar.
AVISAR_ALEM_DA_CRITICA = frozenset({"custo_perto_do_teto"})

# Avisar pelo WhatsApp que o WhatsApp caiu não chega ao Raí, e chama a si mesmo: o aviso
# falha, o fracasso registra outro `evolution_desconectada`, e a pilha acaba. Este
# incidente se lê no log e na tela de saúde (§7), que é onde ele tem de estar.
SEM_AVISO_POR_WHATSAPP = frozenset({"evolution_desconectada"})

ALERTA_PADRAO = "EV-Sales · incidente {gravidade}: {tipo}. Confira em /atendimentos."
ALERTA = {
    "teto_atingido": (
        "EV-Sales · o teto de custo do mês foi atingido ({gasto} de {teto}). A Aurora "
        "parou de responder e as conversas novas vão para a fila humana."
    ),
    "custo_perto_do_teto": (
        "EV-Sales · o custo do mês chegou a {gasto}, mais de 80% do teto de {teto}."
    ),
    "chassi_divergente": (
        "EV-Sales · a Aurora citou um chassi que não veio de consulta. A conversa foi "
        "para atendimento humano — confira em /atendimentos."
    ),
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
    custo_faturado: bool = True,
) -> Trilha:
    linha = Trilha(
        conversa_id=conversa_id,
        tipo=tipo,
        nome=nome,
        dados=_mascarar(dados or {}),
        duracao_ms=duracao_ms,
        custo_micro_reais=custo_micro_reais,
        custo_faturado=custo_faturado,
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
    logger.warning("incidente %s (%s) conversa=%s", tipo, incidente.gravidade, conversa_id)
    alertar(sessao, incidente)
    return incidente


def alertar(sessao: Session, incidente: Incidente) -> int:
    """S-08 §3 e §6 — o "alerta imediato", no canal onde o Raí está: o WhatsApp.

    Mora aqui, e não em cada chamador, porque todo incidente passa por
    `registrar_incidente`: são cinco chamadores hoje e um guarda no lugar por onde todos
    passam é menor que cinco guardas iguais — e não esquece o sexto.

    A dedução do "para quem" também é única: quem tem perfil `dono` e telefone cadastrado
    na tela da S-12. Uma lista de destinatários à parte divergiria de `usuarios` no
    primeiro mês.
    """
    if incidente.tipo in SEM_AVISO_POR_WHATSAPP:
        return 0
    if incidente.gravidade != "critica" and incidente.tipo not in AVISAR_ALEM_DA_CRITICA:
        return 0

    # Import atrasado: `whatsapp` importa este módulo — a Evolution registra incidente
    # quando falha —, e o ciclo no topo do arquivo derruba o app no import. É o mesmo
    # jeito que a `aprovacao.py` já usa para a notificação da S-04 §3.
    from app.whatsapp import avisar_equipe, quem_recebe

    return avisar_equipe(sessao, quem_recebe(sessao, "dono"), _texto_do_alerta(incidente))


def _texto_do_alerta(incidente: Incidente) -> str:
    """Sem PII: `dados` já entrou mascarado, e o que sai daqui é tipo, valor e o que fazer.

    O nome do lead não entra nem mascarado — um alerta é lido no semáforo, e o que o Raí
    precisa saber é o que parou e onde olhar.
    """
    gasto = incidente.dados.get("gasto_micro_reais") if incidente.dados else None
    return ALERTA.get(incidente.tipo, ALERTA_PADRAO).format(
        tipo=incidente.tipo,
        gravidade=incidente.gravidade,
        gasto=dinheiro.formatar(int(gasto) // 10_000) if isinstance(gasto, int) else "—",
        teto=dinheiro.formatar(TETO_MICRO_REAIS // 10_000),
    )


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


def avisar_custo_nao_faturado(sessao: Session) -> None:
    """ADR-012 — o teto só protege o que ele consegue contar.

    Uma vez por dia, para entrar na revisão semanal do Raí. Não corta e não alerta
    ninguém de madrugada: o provedor não faturar é decisão de operação, não incidente.
    O que não pode é a decisão sumir e o painel mostrar R$ 0,00 como se fosse verdade.
    """
    inicio_do_dia = agora().replace(hour=0, minute=0, second=0, microsecond=0)
    if _ja_houve(sessao, "custo_nao_faturado", desde=inicio_do_dia):
        return
    registrar_incidente(
        sessao, None, "custo_nao_faturado", {"gasto_faturado_no_mes": gasto_do_mes(sessao)}
    )
