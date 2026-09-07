"""S-04 — a fila da Neuza, a pausa e a emissão do Espelho.

> O que a Neuza aprova: *"esse carro sai do estoque para esse cliente, por 72h, com esse
> preço escrito."* Não é aprovação de venda — a venda é fechada presencialmente (ADR-011).

Três regras deste arquivo não são detalhe de implementação:

1. **O preço é lido do Postgres pelo servidor**, no instante do pedido e de novo na
   aprovação. Não vem do modelo, não vem do payload, não passa pelo agente (ADR-004).
2. **Se o preço mudou entre o pedido e a decisão, a aprovação aborta.** A Neuza estaria
   aprovando um número que não existe mais, e o cliente receberia um documento com preço
   que a loja não pratica.
3. **O espelho só nasce com `approval_id`.** A coluna é `NOT NULL` com FK: mesmo que este
   arquivo esteja errado, o banco recusa (invariante 3).
"""

import logging
import os
import secrets
import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.arquivos import PREFIXO_DOCUMENTOS, garantir_bucket, guardar
from app.autenticacao import exige_perfil
from app.core.dinheiro import formatar
from app.core.pii import decifrar
from app.db import agora, obter_sessao
from app.espelho import PRAZO_DA_RESERVA, VALIDADE_DA_CONDICAO, gerar
from app.ia.tools.estoque import detalhar_unidade
from app.modelos import Conversa, Espelho, Lead, PedidoDeAprovacao, Unidade, Usuario
from app.observabilidade import registrar

logger = logging.getLogger(__name__)

VALIDADE_DO_PEDIDO = timedelta(minutes=20)

# Para onde o link da notificação aponta. Não é chave de configuração da S-12: a Neuza não
# troca o endereço do sistema pela tela, e o `Enum` de lá é fechado com CHECK no banco de
# propósito. Variável de ambiente, revisada por deploy.
URL_PUBLICA = os.environ.get("EVSALES_URL_PUBLICA", "http://localhost:8010").rstrip("/")
ESCALONAMENTO = timedelta(minutes=15)
MOTIVOS = ("preço", "unidade prometida", "cliente conhecido", "outro")

router = APIRouter()
BancoDeDados = Annotated[Session, Depends(obter_sessao)]
DonoOuGerente = Annotated[Usuario, exige_perfil("dono", "gerente")]


def expirar_vencidos(sessao: Session) -> None:
    """S-04 §6 — pendente que passou de 20 minutos não aparece mais na fila.

    Roda por `UPDATE … WHERE`, não por leitura seguida de gravação: a fila é lida por duas
    pessoas ao mesmo tempo, e conferir em Python deixaria a janela aberta entre as duas.
    """
    sessao.execute(
        update(PedidoDeAprovacao)
        .where(PedidoDeAprovacao.status == "pendente", PedidoDeAprovacao.expira_em <= agora())
        .values(status="expirado")
    )
    sessao.commit()


def escalar_pendentes(sessao: Session) -> int:
    """S-04 §3 — sem decisão em 15 minutos, o Raí também recebe e também pode aprovar.

    `UPDATE … WHERE … RETURNING`, como a expiração: a fila é lida por duas pessoas ao mesmo
    tempo, e marcar em Python deixaria a mesma notificação sair duas vezes. O `RETURNING` é
    o que diz **quais** foram escalados agora — avisar sobre os que já estavam escalados
    mandaria o mesmo pedido ao Raí a cada 5 minutos, e na décima segunda vez ele silencia
    o número.
    """
    escalados = list(
        sessao.scalars(
            update(PedidoDeAprovacao)
            .where(
                PedidoDeAprovacao.status == "pendente",
                PedidoDeAprovacao.escalado_em.is_(None),
                PedidoDeAprovacao.criado_em <= agora() - ESCALONAMENTO,
            )
            .values(escalado_em=agora())
            .returning(PedidoDeAprovacao)
        )
    )
    sessao.commit()

    for pedido in escalados:
        notificar(
            sessao,
            pedido,
            perfil="dono",
            abertura="A Neuza ainda não decidiu, e já faz 15 minutos.",
        )
        registrar(sessao, pedido.conversa_id, "evento", "aprovacao_escalada")
    return len(escalados)


def solicitar_aprovacao(sessao: Session, conversa: Conversa, chassi: str) -> dict[str, object]:
    """A tool da etapa `condicao` (S-04 §2). **Para o fluxo e não emite nada.**

    O turno termina normalmente: não há processo suspenso, o estado é a linha no banco
    (ADR-009).
    """
    unidade = sessao.get(Unidade, chassi)
    if unidade is None or unidade.status != "disponivel":
        # A Aurora retoma a recomendação sem parar a conversa.
        return {"resultado": "indisponivel", "chassi": chassi}

    agora_ = agora()
    pedido = PedidoDeAprovacao(
        conversa_id=conversa.id,
        chassi=chassi,
        lead_id=conversa.lead_id,
        # Lido do banco neste instante. Nunca do texto do modelo.
        preco_centavos=unidade.preco_centavos,
        qualificacao_resumo=dict(conversa.qualificacao),
        codigo=secrets.token_urlsafe(6)[:8],
        trace_id=conversa.trace_id,
        criado_em=agora_,
        expira_em=agora_ + VALIDADE_DO_PEDIDO,
    )
    sessao.add(pedido)
    conversa.etapa = "aguardando_aprovacao"
    sessao.commit()

    registrar(
        sessao,
        conversa.id,
        "evento",
        "aprovacao_solicitada",
        dados={"chassi": chassi, "preco_centavos": unidade.preco_centavos},
    )
    notificar(sessao, pedido)
    return {
        "resultado": "aguardando_aprovacao",
        "expira_em_minutos": int(VALIDADE_DO_PEDIDO.total_seconds() // 60),
    }


def resumo_para_notificacao(sessao: Session, pedido: PedidoDeAprovacao) -> str:
    """S-04 §3 — o que a Neuza lê no celular antes de decidir.

    **Nome mascarado, telefone ausente** (ADR-007, invariante 5). O link leva ao card e
    não substitui o login: quem confere a sessão é a API da página de destino.
    """
    unidade = sessao.get(Unidade, pedido.chassi)
    lead = sessao.get(Lead, pedido.lead_id)
    quem = lead.nome_mascarado() if lead else "cliente"
    carro = (
        f"{unidade.marca} {unidade.modelo} {unidade.versao} · {unidade.cor}"
        if unidade
        else pedido.chassi
    )
    return "\n".join(
        [
            "*Reserva para aprovar — Sol & Volt*",
            "",
            quem,
            f"{carro} · chassi …{pedido.chassi[-4:]}",
            f"{formatar(pedido.preco_centavos)}  ·  sai do estoque por 72h",
            "",
            f"Decidir: {URL_PUBLICA}/a/{pedido.codigo}",
        ]
    )


def notificar(
    sessao: Session, pedido: PedidoDeAprovacao, perfil: str = "gerente", abertura: str = ""
) -> int:
    """Manda para quem tem aquele perfil e telefone cadastrado (S-12 §3).

    Import tardio: `whatsapp` importa `conversas`, que importa daqui — o import no topo
    fecharia o ciclo. É o preço de a notificação morar onde o pedido nasce, que é onde ela
    não é esquecida.
    """
    from app.whatsapp import avisar_equipe, quem_recebe

    pessoas = quem_recebe(sessao, perfil)
    if not pessoas:
        # Sem telefone cadastrado a fila continua funcionando: a decisão vive em
        # /aprovacoes, e o WhatsApp é o atalho, não o caminho.
        logger.warning("aprovação pendente sem notificação: nenhum %s com telefone", perfil)
        return 0
    texto = resumo_para_notificacao(sessao, pedido)
    if abertura:
        texto = f"{abertura}\n\n{texto}"
    return avisar_equipe(sessao, pessoas, texto)


def _numero_do_espelho(sessao: Session) -> str:
    """`SV-2026-0001`, de uma sequência do Postgres.

    Sequência e não `count(*) + 1`: duas aprovações no mesmo segundo receberiam o mesmo
    número, e número repetido num documento é o tipo de erro que só aparece na frente do
    cliente. A sequência **não reinicia por ano** — o ano no prefixo é a data de emissão,
    e `SV-2027-0312` é o 312º espelho da história. Não há exigência de numeração fiscal
    porque isto não é documento fiscal (ADR-011).
    """
    proximo = sessao.scalar(select(func.nextval("espelhos_numero_seq")))
    return f"SV-{agora().year}-{int(proximo or 0):04d}"


def _emitir_espelho(sessao: Session, pedido: PedidoDeAprovacao, quem_aprovou: Usuario) -> Espelho:
    unidade = detalhar_unidade(sessao, pedido.chassi)
    lead = sessao.get(Lead, pedido.lead_id)
    if unidade is None or lead is None:
        raise HTTPException(409, detail={"mensagem": "A unidade saiu do estoque."})

    agora_ = agora()
    numero = _numero_do_espelho(sessao)
    # O nome completo entra no documento do próprio cliente — é o terceiro chamador
    # autorizado de `decifrar` (S-09 §3), e o PDF vai para ele, não para um log.
    try:
        nome_do_cliente = decifrar(lead.nome_cifrado)
    except Exception:
        # Aqui o nome completo é obrigatório: é o documento do cliente. Falhar legível é
        # melhor do que emitir um Espelho com "?" no lugar de quem está comprando.
        raise HTTPException(
            409, detail={"mensagem": "Não foi possível ler o cadastro do cliente."}
        ) from None

    pdf = gerar(
        numero=numero,
        nome_do_cliente=nome_do_cliente,
        unidade=unidade,
        preco_centavos=pedido.preco_centavos,
        aprovado_por=quem_aprovou.nome,
        emitido_em=agora_,
    )
    objeto = f"{PREFIXO_DOCUMENTOS}espelho-{numero}.pdf"
    garantir_bucket()
    guardar(objeto, pdf, "application/pdf")

    espelho = Espelho(
        conversa_id=pedido.conversa_id,
        chassi=pedido.chassi,
        approval_id=pedido.id,  # ← invariante 3; a coluna é NOT NULL com FK
        preco_centavos=pedido.preco_centavos,
        numero=numero,
        pdf_objeto=objeto,
        valido_ate=agora_ + VALIDADE_DA_CONDICAO,
        emitido_em=agora_,
    )
    sessao.add(espelho)
    return espelho


class Recusa(BaseModel):
    motivo: str


def _pedido_pendente(sessao: Session, pedido_id: uuid.UUID) -> PedidoDeAprovacao:
    expirar_vencidos(sessao)
    pedido = sessao.get(PedidoDeAprovacao, pedido_id)
    if pedido is None or pedido.status != "pendente":
        raise HTTPException(409, detail={"mensagem": "Esse pedido não está mais aguardando."})
    return pedido


@router.get("/api/aprovacoes")
def fila(sessao: BancoDeDados, _: DonoOuGerente) -> list[dict[str, object]]:
    """S-04 §4 — um card por pedido, mais antigo primeiro. Sem filtro e sem paginação."""
    expirar_vencidos(sessao)
    escalar_pendentes(sessao)
    pedidos = sessao.scalars(
        select(PedidoDeAprovacao)
        .where(PedidoDeAprovacao.status == "pendente")
        .order_by(PedidoDeAprovacao.criado_em)
    ).all()

    cards = []
    for pedido in pedidos:
        unidade = sessao.get(Unidade, pedido.chassi)
        lead = sessao.get(Lead, pedido.lead_id)
        cards.append(
            {
                "id": str(pedido.id),
                # Nome mascarado, telefone ausente (ADR-007). A tela da decisão não
                # precisa do telefone para decidir, então ele não sai do banco.
                "cliente": lead.nome_mascarado() if lead else "?",
                "veiculo": f"{unidade.marca} {unidade.modelo} {unidade.versao}" if unidade else "?",
                "cor": unidade.cor if unidade else "?",
                "chassi_final": pedido.chassi[-4:],
                "preco_centavos": pedido.preco_centavos,
                "qualificacao": pedido.qualificacao_resumo,
                "segundos_desde_o_pedido": int((agora() - pedido.criado_em).total_seconds()),
                "escalado": pedido.escalado_em is not None,
            }
        )
    return cards


@router.post("/api/aprovacoes/{pedido_id}/aprovar")
def aprovar(
    pedido_id: uuid.UUID, requisicao: Request, sessao: BancoDeDados, quem: DonoOuGerente
) -> dict[str, object]:
    """S-04 §5 — a aprovação e a emissão, na mesma transação.

    Sem confirmação de propósito (§4): confirmação dobra o tempo e treina o toque
    automático. A reversão existe na tela do vendedor enquanto o cliente não recebeu o
    documento.
    """
    pedido = _pedido_pendente(sessao, pedido_id)
    unidade = sessao.get(Unidade, pedido.chassi)

    if unidade is None or unidade.status != "disponivel":
        pedido.status = "expirado"
        sessao.commit()
        raise HTTPException(409, detail={"mensagem": "O carro saiu do estoque. Pedido cancelado."})

    if unidade.preco_centavos != pedido.preco_centavos:
        # §5 passo 2 — a tabela mudou durante a espera. Aprovar aqui emitiria um documento
        # com um preço que a loja não pratica mais. Aborta, e refaz o pedido com o novo.
        pedido.status = "expirado"
        agora_ = agora()
        novo = PedidoDeAprovacao(
            conversa_id=pedido.conversa_id,
            chassi=pedido.chassi,
            lead_id=pedido.lead_id,
            preco_centavos=unidade.preco_centavos,
            qualificacao_resumo=pedido.qualificacao_resumo,
            codigo=secrets.token_urlsafe(6)[:8],
            trace_id=pedido.trace_id,
            criado_em=agora_,
            expira_em=agora_ + VALIDADE_DO_PEDIDO,
        )
        sessao.add(novo)
        sessao.commit()
        registrar(
            sessao,
            pedido.conversa_id,
            "evento",
            "preco_mudou_na_espera",
            dados={
                "de_centavos": pedido.preco_centavos,
                "para_centavos": unidade.preco_centavos,
                "pedido_novo": str(novo.id),
            },
        )
        raise HTTPException(
            409,
            detail={
                "mensagem": "O preço mudou desde o pedido. Refiz com o valor novo.",
                "pedido_novo": str(novo.id),
            },
        )

    agora_ = agora()
    pedido.status = "aprovado"
    pedido.decidido_por = quem.id
    pedido.decidido_em = agora_
    pedido.ip_da_decisao = requisicao.client.host if requisicao.client else None
    espelho = _emitir_espelho(sessao, pedido, quem)

    conversa = sessao.get(Conversa, pedido.conversa_id)
    if conversa is not None:
        conversa.etapa = "reserva"  # S-05 assume daqui
    sessao.commit()

    registrar(
        sessao,
        pedido.conversa_id,
        "evento",
        "aprovado",
        dados={
            "por": quem.nome,
            "pedido_id": str(pedido.id),
            "espelho": espelho.numero,
            "preco_centavos": pedido.preco_centavos,
        },
    )
    return {
        "espelho": espelho.numero,
        "valido_ate": espelho.valido_ate.isoformat(),
        "reserva_horas": int(PRAZO_DA_RESERVA.total_seconds() // 3600),
    }


@router.post("/api/aprovacoes/{pedido_id}/recusar")
def recusar(
    recusa: Recusa,
    pedido_id: uuid.UUID,
    requisicao: Request,
    sessao: BancoDeDados,
    quem: DonoOuGerente,
) -> dict[str, str]:
    """S-04 §6 — o motivo vai para o vendedor, **nunca para o cliente**.

    A Aurora não improvisa justificativa: a conversa vai para humano, e a gerência fala
    diretamente. Inventar explicação de recusa é como se perde a confiança que o produto
    inteiro existe para construir.
    """
    if recusa.motivo not in MOTIVOS:
        raise HTTPException(400, detail={"mensagem": f"Motivo precisa ser um de {MOTIVOS}."})

    pedido = _pedido_pendente(sessao, pedido_id)
    pedido.status = "rejeitado"
    pedido.decidido_por = quem.id
    pedido.decidido_em = agora()
    pedido.ip_da_decisao = requisicao.client.host if requisicao.client else None
    pedido.motivo_rejeicao = recusa.motivo

    conversa = sessao.get(Conversa, pedido.conversa_id)
    if conversa is not None:
        conversa.modo = "humano"
        conversa.etapa = "humano"
    sessao.commit()

    registrar(
        sessao,
        pedido.conversa_id,
        "evento",
        "recusado",
        dados={"por": quem.nome, "motivo": recusa.motivo, "pedido_id": str(pedido.id)},
    )
    return {"mensagem": "Recusado. Um vendedor assume a conversa."}


@router.get("/api/espelhos/{espelho_id}")
def baixar_espelho(
    espelho_id: uuid.UUID, sessao: BancoDeDados, _: DonoOuGerente
) -> dict[str, str]:
    """A URL assinada do ADR-013, válida por 15 minutos. Nunca uma URL pública."""
    from app.arquivos import url_assinada_do_documento

    espelho = sessao.get(Espelho, espelho_id)
    if espelho is None:
        raise HTTPException(404, detail={"mensagem": "Não encontrado."})
    return {
        "numero": espelho.numero,
        "url": url_assinada_do_documento(espelho.pdf_objeto.removeprefix(PREFIXO_DOCUMENTOS)),
    }
