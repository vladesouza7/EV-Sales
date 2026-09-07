"""S-02 §3 e S-03 — o turno: gera, verifica, e só então grava e entrega.

A ordem é a proteção, e ela não mudou quando o modelo entrou: nenhum token sai antes da
verificação numérica passar (ADR-003), e a mensagem só passa a existir em `mensagens`
depois de aprovada. Texto reprovado não é gravado, não é fatiado e não aparece no evento
de erro — nem parcialmente.

Sem framework de orquestração (ADR-009): o estado é a linha no banco, o loop é um `while`
com teto, e a etapa decide as tools. Uma máquina de estados que cabe na cabeça de quem for
depurar isso às onze da noite.
"""

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.pii import decifrar, redigir
from app.db import agora
from app.ia.provedor import ProvedorCompativel, ProvedorIndisponivel, ProvedorLLM
from app.ia.registro import disponiveis, esquemas, executar
from app.ia.verificacao import Veredito, permitidos_de, verificar
from app.modelos import Conversa, Lead, Mensagem, Unidade
from app.observabilidade import (
    MENSAGEM_NO_TETO,
    avisar_custo_nao_faturado,
    marcar_se_conversa_cara,
    pode_chamar_llm,
    registrar,
    registrar_incidente,
)

logger = logging.getLogger(__name__)

Evento = tuple[str, dict[str, object]]

VERSAO_DO_PROMPT = "aurora_v1"
_PROMPT = (Path(__file__).parent / "prompts" / f"{VERSAO_DO_PROMPT}.md").read_text("utf-8")

# S-03 §6 — os tetos do loop. Estourar não é erro: é o turno terminando com o que tem.
MAX_TOOL_CALLS = 6
MAX_REGENERACOES = 1
MENSAGENS_DE_HISTORICO = 20
# S-03 §3 — no máximo 2 perguntas antes da primeira recomendação. A regra é de código:
# no prompt ela dependeria de o modelo saber contar as próprias perguntas.
#
# São 2 e não 3 porque a transição roda no FIM do turno: com 3, a Aurora perguntava na
# saudação, no turno 2 e no turno 3, e só recomendava no 4 — três perguntas. A persona 1
# abandona interrogatório, e ela abandona antes de chegar ao quarto turno.
MAX_MENSAGENS_EM_QUALIFICACAO = 2

PEDIDO_DESCULPA = (
    "Deixa eu chamar um dos nossos consultores pra te responder isso com precisão. "
    "Já já alguém fala com você por aqui."
)

ORIENTACAO_POR_ETAPA = {
    "saudacao": "Cumprimente pelo nome e pergunte uma coisa só sobre o uso do carro.",
    "qualificacao": "Descubra o uso e a rotina. Uma pergunta por mensagem, duas no total.",
    "recomendacao": "Mostre no máximo 3 carros do estoque, sempre consultando as tools antes.",
    "objecao": "Responda com número que veio de tool. Sem tool, diga que vai confirmar.",
    "condicao": "O cliente escolheu um carro. Quem aprova condição é a Neuza.",
    "aguardando_aprovacao": "Você está esperando a Neuza. Não prometa nada enquanto isso.",
    "reserva": "A condição foi aprovada. Confirme o carro reservado.",
    "test_drive": "Combine dia e horário do test drive.",
    "humano": "Um vendedor assumiu a conversa.",
    "encerrada": "A conversa terminou.",
}

# S-12 §6 — o provedor é montado **por turno**, a partir da configuração em uso. Uma
# instância de módulo lida no import faria a troca na tela só valer depois de reiniciar a
# API, que é exatamente o passo que a S-12 existe para eliminar.
#
# `PROVEDOR` continua existindo como **o** ponto de substituição: a suíte inteira troca ele
# por um dublê (`conftest`, fixture autouse), e `provedor_atual` respeita a troca. Mover
# essa costura sem manter um ponto único faria 273 testes falarem com a rede de verdade.
PROVEDOR: ProvedorLLM | None = None


def provedor_atual(sessao: Session) -> ProvedorLLM:
    if PROVEDOR is not None:
        return PROVEDOR
    from app.configuracao import ambiente

    return ProvedorCompativel(ambiente(sessao))


def verificar_numeros(texto: str, permitidos: set[tuple[str, float]], do_cliente: str) -> Veredito:
    """S-03 §4, no lugar certo da ordem: antes da gravação e antes do primeiro token."""
    return verificar(texto, permitidos, do_cliente)


def _fatiar(texto: str) -> Iterator[str]:
    """O buffer da §3 sendo esvaziado, não o modelo digitando.

    A resposta só é fatiada depois de aprovada: o cliente vê o indicador de digitação
    durante a geração, e texto só depois que ele não pode mais ser retirado.
    """
    for pedaco in texto.split(" "):
        yield pedaco + " "


def _primeiro_nome(sessao: Session, conversa: Conversa) -> str:
    """S-09 §4: no prompt entra o primeiro nome, e só. O telefone não entra nunca."""
    lead = sessao.get(Lead, conversa.lead_id)
    if lead is None:
        return "cliente"
    try:
        return decifrar(lead.nome_cifrado).split()[0]
    except Exception:
        return "cliente"


def _sistema(sessao: Session, conversa: Conversa) -> str:
    return _PROMPT.format(
        etapa=conversa.etapa,
        orientacao_da_etapa=ORIENTACAO_POR_ETAPA[conversa.etapa],
        qualificacao=json.dumps(conversa.qualificacao, ensure_ascii=False) or "nada ainda",
        primeiro_nome=_primeiro_nome(sessao, conversa),
    )


def _do_cliente(conteudo: str) -> str:
    """S-03 §7 — a mensagem entra rotulada e delimitada.

    Isto é reforço, não garantia. A garantia é que a tool de desconto não existe e que as
    demais são filtradas por etapa: um pedido de 30% não tem função para chamar.
    """
    return f"<mensagem_do_cliente>\n{redigir(conteudo)}\n</mensagem_do_cliente>"


def _anteriores(sessao: Session, conversa: Conversa) -> list[Mensagem]:
    recentes = sessao.scalars(
        select(Mensagem)
        .where(Mensagem.conversa_id == conversa.id, Mensagem.processada_em.isnot(None))
        .order_by(Mensagem.criada_em.desc(), Mensagem.id)
        .limit(MENSAGENS_DE_HISTORICO)
    ).all()
    return list(reversed(recentes))


def _historico(anteriores: list[Mensagem]) -> list[dict[str, object]]:
    return [
        {
            "role": "assistant" if m.direcao == "saida" else "user",
            "content": m.conteudo if m.direcao == "saida" else _do_cliente(m.conteudo),
        }
        for m in anteriores
    ]


def _falas_do_cliente(anteriores: list[Mensagem], entrada: Mensagem) -> str:
    """S-03 §4, condição 3 — e ela vale para a conversa inteira, não só para o turno.

    A spec dizia "neste turno". Numa conversa real isso reprova a Aurora justamente
    quando ela faz o que o CASE pede: a cliente disse "40 km por dia" no turno 2, a
    Aurora repetiu "40 km por dia" no turno 4 para traduzir a especificação em rotina, e
    a verificação bloqueou um número que **a própria cliente tinha fornecido**.

    Preço e autonomia expiram — o chassi muda de status, o preço muda. O que o cliente
    contou sobre a rotina dele não expira, e não há como a Aurora inventar o que ele
    mesmo falou. Por isso a janela aqui é a conversa, e não o turno.
    """
    falas = [m.conteudo for m in anteriores if m.direcao == "entrada"]
    return " ".join([*falas, entrada.conteudo])


def _modelo_citado(sessao: Session, texto: str) -> bool:
    """Persona 3 (Dr. Almir): quem já disse o modelo e pediu preço não é interrogado.

    Regra de código, não de prompt — a S-03 §3 exige pular a qualificação, e depender do
    modelo para reconhecer a própria pressa é depender justamente do que falha sob pressa.
    """
    minusculo = texto.lower()
    modelos = sessao.scalars(
        select(Unidade.modelo).where(Unidade.status == "disponivel").distinct()
    )
    return any(modelo.lower() in minusculo for modelo in modelos)


def _resumo(nome: str, resultado: object) -> str:
    if isinstance(resultado, list):
        return f"{len(resultado)} unidades"
    if isinstance(resultado, dict) and isinstance(resultado.get("unidades"), list):
        return f"{len(resultado['unidades'])} unidades"
    return nome.replace("_", " ")


def _fichas_do(resultado: object) -> list[dict[str, object]]:
    """O que a tool devolveu, em forma de ficha, para alimentar os números permitidos."""
    if isinstance(resultado, list):
        return [f for f in resultado if isinstance(f, dict)]
    if isinstance(resultado, dict):
        internas = resultado.get("unidades")
        if isinstance(internas, list):
            return [f for f in internas if isinstance(f, dict)]
        return [resultado] if "preco_centavos" in resultado else []
    return []


def _entradas(sessao: Session, conversa_id: uuid.UUID) -> int:
    return len(
        sessao.scalars(
            select(Mensagem.id).where(
                Mensagem.conversa_id == conversa_id, Mensagem.direcao == "entrada"
            )
        ).all()
    )


def _etapa_antes_do_turno(sessao: Session, conversa: Conversa, entrada: Mensagem) -> None:
    if conversa.etapa in ("saudacao", "qualificacao") and _modelo_citado(sessao, entrada.conteudo):
        conversa.etapa = "recomendacao"


def _etapa_depois_do_turno(sessao: Session, conversa: Conversa) -> None:
    """ADR-009: a transição é regra de código. O modelo nunca declara que mudou de etapa —
    se pudesse, escolheria as tools do turno seguinte."""
    if conversa.etapa == "saudacao":
        conversa.etapa = "qualificacao"
    elif conversa.etapa == "qualificacao" and (
        conversa.qualificacao or _entradas(sessao, conversa.id) >= MAX_MENSAGENS_EM_QUALIFICACAO
    ):
        conversa.etapa = "recomendacao"


def _gravar_saida(
    sessao: Session, conversa: Conversa, entrada: Mensagem, texto: str, *, da_ia: bool
) -> Mensagem:
    agora_ = agora()
    saida = Mensagem(
        conversa_id=conversa.id,
        direcao="saida",
        autor="aurora",
        canal=conversa.canal_atual,
        conteudo=texto,
        gerada_por_ia=da_ia,
        processada_em=agora_,
    )
    sessao.add(saida)
    entrada.processada_em = agora_
    conversa.ultima_mensagem_em = agora_
    sessao.commit()
    return saida


def _degradar(
    sessao: Session, conversa: Conversa, entrada: Mensagem, motivo: str, comeco: float
) -> list[Evento]:
    """A Aurora sai e um humano entra, com aviso honesto ao cliente.

    Vale para o teto de custo e para o provedor fora do ar: nos dois casos a alternativa
    seria responder sem consultar, que é exatamente o risco que o projeto existe para
    eliminar. Degradar para humano é ruim; inventar preço encerra o projeto.
    """
    conversa.modo = "humano"
    conversa.etapa = "humano"
    saida = _gravar_saida(sessao, conversa, entrada, MENSAGEM_NO_TETO, da_ia=False)
    registrar(
        sessao, conversa.id, "evento", motivo, dados={}, duracao_ms=_ms(comeco)
    )
    eventos: list[Evento] = [("token", {"texto": p}) for p in _fatiar(MENSAGEM_NO_TETO)]
    eventos.append(("mensagem_fim", {"mensagem_id": str(saida.id), "etapa": conversa.etapa}))
    return eventos


def _ms(desde: float) -> int:
    return int((time.monotonic() - desde) * 1000)


async def executar_turno(
    sessao: Session, conversa: Conversa, entrada: Mensagem
) -> AsyncIterator[Evento]:
    comeco = time.monotonic()

    # S-08 §3 — o teto corta antes de qualquer tool e antes de qualquer token.
    if not pode_chamar_llm(sessao):
        for evento in _degradar(sessao, conversa, entrada, "teto_atingido", comeco):
            yield evento
        return

    provedor = provedor_atual(sessao)
    if not provedor.configurado():
        logger.error("provedor de LLM não configurado (ADR-012): turno degradado para humano")
        for evento in _degradar(sessao, conversa, entrada, "provedor_nao_configurado", comeco):
            yield evento
        return

    _etapa_antes_do_turno(sessao, conversa, entrada)
    etapa_do_turno = conversa.etapa

    anteriores = _anteriores(sessao, conversa)
    do_cliente = _falas_do_cliente(anteriores, entrada)
    mensagens: list[dict[str, object]] = [
        {"role": "system", "content": _sistema(sessao, conversa)},
        *_historico(anteriores),
        {"role": "user", "content": _do_cliente(entrada.conteudo)},
    ]
    fichas: list[dict[str, object]] = []
    custo = 0
    chamadas = 0
    texto = ""

    while True:
        # S-03 §6 — no teto de tool calls o turno para de oferecer tools em vez de
        # descartar a chamada seguinte: sem tool na mesa, o modelo escreve a resposta com
        # o que já consultou, que é o que "encerra o turno com o que tem" quer dizer.
        oferecidas = esquemas(conversa.etapa) if chamadas < MAX_TOOL_CALLS else []
        try:
            resposta = await provedor.conversar(mensagens, oferecidas)
        except ProvedorIndisponivel:
            for evento in _degradar(sessao, conversa, entrada, "provedor_indisponivel", comeco):
                yield evento
            return

        custo += resposta.custo_micro_reais
        if not resposta.tools or not oferecidas:
            texto = resposta.texto
            break

        mensagens.append(
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.nome, "arguments": json.dumps(c.argumentos)},
                    }
                    for c in resposta.tools
                ],
            }
        )
        for chamada in resposta.tools:
            chamadas += 1
            yield "tool_inicio", {"nome": chamada.nome}
            inicio_da_tool = time.monotonic()
            try:
                resultado: object = executar(sessao, conversa, chamada.nome, chamada.argumentos)
            except (PermissionError, ValueError, KeyError) as erro:
                # Nome inventado, etapa errada ou argumento inválido não derrubam o turno:
                # o modelo recebe a recusa como retorno e segue sem aquele número.
                resultado = {"erro": type(erro).__name__}
            fichas.extend(_fichas_do(resultado))
            # O span guarda argumentos e retorno inteiros, não um resumo: é ele que prova
            # de onde veio cada número da resposta, e resumo não prova nada (ADR-006).
            registrar(
                sessao,
                conversa.id,
                "tool",
                chamada.nome,
                dados={"argumentos": chamada.argumentos, "retorno": resultado},
                duracao_ms=_ms(inicio_da_tool),
            )
            mensagens.append(
                {
                    "role": "tool",
                    "tool_call_id": chamada.id,
                    "content": json.dumps(resultado, ensure_ascii=False, default=str),
                }
            )
            yield "tool_fim", {"nome": chamada.nome, "resumo": _resumo(chamada.nome, resultado)}

        if conversa.modo != "aurora":
            # `transferir_para_humano` rodou. O turno acaba **aqui**, com frase nossa.
            #
            # Continuar o loop era pedir mais uma fala a quem já saiu da conversa — e foi
            # assim que o retorno da tool, em JSON cru, chegou ao cliente: sem tool na mesa
            # e sem nada a dizer, o modelo ecoou o próprio resultado da tool como resposta.
            # Em modo humano a Aurora não responde (S-02 §2), e isso vale para o turno em
            # que ela sai, não só para os seguintes.
            saida = _gravar_saida(sessao, conversa, entrada, PEDIDO_DESCULPA, da_ia=False)
            registrar(
                sessao,
                conversa.id,
                "evento",
                "transferido_para_humano",
                dados={"etapa": etapa_do_turno},
                duracao_ms=_ms(comeco),
                custo_micro_reais=custo,
                custo_faturado=resposta.custo_faturado,
            )
            for pedaco in _fatiar(PEDIDO_DESCULPA):
                yield "token", {"texto": pedaco}
            yield "mensagem_fim", {"mensagem_id": str(saida.id), "etapa": conversa.etapa}
            return

    if not texto.strip():
        # S-03 §6 — estourou o teto de tool calls, ou o modelo devolveu só chamada e
        # nenhuma prosa. Uma última passada sem tools encerra o turno com o que tem,
        # em vez de entregar mensagem em branco ao cliente.
        try:
            resposta = await provedor.conversar(mensagens, [])
        except ProvedorIndisponivel:
            for evento in _degradar(sessao, conversa, entrada, "provedor_indisponivel", comeco):
                yield evento
            return
        custo += resposta.custo_micro_reais
        texto = resposta.texto

    if not texto.strip():
        for evento in _degradar(sessao, conversa, entrada, "resposta_vazia", comeco):
            yield evento
        return

    permitidos = permitidos_de(fichas)
    veredito = verificar_numeros(texto, permitidos, do_cliente)
    tentativas = 0

    while not veredito.aprovado and tentativas < MAX_REGENERACOES:
        tentativas += 1
        mensagens.append({"role": "assistant", "content": texto})
        mensagens.append(
            {
                "role": "user",
                "content": (
                    "Sua resposta citou números que não vieram de consulta nenhuma: "
                    f"{', '.join(veredito.divergentes)}. Reescreva usando apenas os valores "
                    "que as tools devolveram neste turno, ou sem citar número nenhum."
                ),
            }
        )
        try:
            resposta = await provedor.conversar(mensagens, [])
        except ProvedorIndisponivel:
            for evento in _degradar(sessao, conversa, entrada, "provedor_indisponivel", comeco):
                yield evento
            return
        custo += resposta.custo_micro_reais
        texto = resposta.texto
        veredito = verificar_numeros(texto, permitidos, do_cliente)

    registrar(
        sessao,
        conversa.id,
        "verificacao",
        "numeros",
        dados={
            "extraidos": veredito.extraidos,
            "divergentes": veredito.divergentes,
            "permitidos": sorted(f"{u}:{v}" for u, v in permitidos),
            "veredito": "aprovado" if veredito.aprovado else "reprovado",
            "regeneracoes": tentativas,
        },
    )

    if not veredito.aprovado:
        # S-03 §4 passo 3: bloqueia e transfere. O texto reprovado não é gravado, não é
        # fatiado e não aparece no evento de erro — nem parcialmente.
        conversa.modo = "humano"
        conversa.etapa = "humano"
        entrada.processada_em = agora()
        sessao.commit()
        registrar_incidente(
            sessao,
            conversa.id,
            "numero_divergente",
            {"etapa": etapa_do_turno, "divergentes": veredito.divergentes},
        )
        yield "erro", {"codigo": "numero_divergente", "mensagem_ao_cliente": PEDIDO_DESCULPA}
        return

    saida = _gravar_saida(sessao, conversa, entrada, texto, da_ia=True)
    if conversa.modo == "aurora":
        # `transferir_para_humano` já mudou a etapa; a transição normal não desfaz isso.
        _etapa_depois_do_turno(sessao, conversa)
    sessao.commit()

    registrar(
        sessao,
        conversa.id,
        "turno",
        "aurora",
        dados={
            "etapa": etapa_do_turno,
            "modelo": resposta.modelo,
            "versao_do_prompt": VERSAO_DO_PROMPT,
            "tools": list(disponiveis(etapa_do_turno)),
            "tool_calls": chamadas,
        },
        duracao_ms=_ms(comeco),
        custo_micro_reais=custo,
        custo_faturado=resposta.custo_faturado,
    )
    marcar_se_conversa_cara(sessao, conversa.id)
    if not resposta.custo_faturado:
        avisar_custo_nao_faturado(sessao)

    for pedaco in _fatiar(texto):
        yield "token", {"texto": pedaco}
    yield "mensagem_fim", {"mensagem_id": str(saida.id), "etapa": conversa.etapa}
