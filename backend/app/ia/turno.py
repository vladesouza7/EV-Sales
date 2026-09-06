"""S-02 §3 — o turno: gera, verifica, e só então grava e entrega.

O modelo entra na S-03. O que a S-02 fixa é a **ordem**, e é ela que protege o Raí:
nenhum token sai antes da verificação numérica passar (ADR-003), e a mensagem só passa
a existir em `mensagens` depois de aprovada. Trocar a geração provisória por uma
chamada ao modelo não pode mudar essa ordem — se mudar, o texto reprovado vaza.

A S-08 acrescentou o que envolve essa ordem: o teto de custo é conferido **antes** de
qualquer coisa acontecer, e cada passo do turno deixa linha na trilha (ADR-006). A
instrumentação veio primeiro de propósito — quando a S-03 ligar o modelo, o lugar onde
o prompt, a tool e o veredito são gravados já existe.
"""

import time
from collections.abc import AsyncIterator, Iterator

from sqlalchemy.orm import Session

from app.db import agora
from app.ia.etapas import tools_da_etapa
from app.ia.tools.estoque import buscar_unidades
from app.modelos import Conversa, Mensagem
from app.observabilidade import (
    MENSAGEM_NO_TETO,
    marcar_se_conversa_cara,
    pode_chamar_llm,
    registrar,
    registrar_incidente,
)

Evento = tuple[str, dict[str, object]]

# ponytail: resposta fixa até a S-03 ligar o modelo. Não cita número nenhum de
# propósito — enquanto a verificação da §4 é um stub, texto sem número é o único
# texto que não pode estar errado.
RESPOSTA_PROVISORIA = (
    "Oi! Sou a Aurora, consultora da Sol & Volt, aqui em Tambaú. "
    "Me conta como você usa o carro no dia a dia que eu já vejo o que temos na loja."
)

PEDIDO_DESCULPA = (
    "Deixa eu chamar um dos nossos consultores pra te responder isso com precisão. "
    "Já já alguém fala com você por aqui."
)


def verificar_numeros(texto: str, permitidos: set[object]) -> bool:
    """S-03 §4 — extração por regex e conferência contra os retornos de tool do turno.

    Aqui só existe o ponto de chamada, no lugar certo da ordem. A S-03 preenche o
    corpo; o que a S-02 garante é que ele é chamado **antes** da gravação e antes do
    primeiro `token`.
    """
    return True


def _fatiar(texto: str) -> Iterator[str]:
    """O buffer da §3 sendo esvaziado, não o modelo digitando.

    A resposta só é fatiada depois de aprovada: o cliente vê o indicador de digitação
    durante a geração, e texto só depois que ele não pode mais ser retirado.
    """
    for pedaco in texto.split(" "):
        yield pedaco + " "


def _proxima_etapa(etapa: str) -> str:
    """S-02 §2 — transição por regra de código. `saudacao` é a primeira mensagem, só."""
    return "qualificacao" if etapa == "saudacao" else etapa


def _ms(desde: float) -> int:
    return int((time.monotonic() - desde) * 1000)


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


async def executar_turno(
    sessao: Session, conversa: Conversa, entrada: Mensagem
) -> AsyncIterator[Evento]:
    comeco = time.monotonic()
    etapa_inicial = conversa.etapa

    if not pode_chamar_llm(sessao):
        # S-08 §3 — o teto corta antes de qualquer tool e antes de qualquer token. A
        # conversa vai para a fila humana com o aviso honesto; a Aurora não tenta e falha.
        conversa.modo = "humano"
        conversa.etapa = "humano"
        saida = _gravar_saida(sessao, conversa, entrada, MENSAGEM_NO_TETO, da_ia=False)
        registrar(
            sessao,
            conversa.id,
            "evento",
            "teto_atingido",
            dados={"etapa": etapa_inicial},
            duracao_ms=_ms(comeco),
        )
        for pedaco in _fatiar(MENSAGEM_NO_TETO):
            yield "token", {"texto": pedaco}
        yield "mensagem_fim", {"mensagem_id": str(saida.id), "etapa": conversa.etapa}
        return

    permitidos: set[object] = set()
    tools = tools_da_etapa(conversa.etapa)

    if "buscar_unidades" in tools:
        yield "tool_inicio", {"nome": "buscar_unidades"}
        inicio_da_tool = time.monotonic()
        unidades = buscar_unidades(sessao)
        for unidade in unidades:
            permitidos.add(unidade["preco_centavos"])
            permitidos.add(unidade["autonomia_km"])
        # O span guarda o retorno inteiro, não um resumo: é ele que prova de onde veio
        # cada número da resposta, e resumo não prova nada (ADR-006).
        registrar(
            sessao,
            conversa.id,
            "tool",
            "buscar_unidades",
            dados={"argumentos": {}, "retorno": unidades},
            duracao_ms=_ms(inicio_da_tool),
        )
        yield "tool_fim", {"nome": "buscar_unidades", "resumo": f"{len(unidades)} unidades"}

    texto = RESPOSTA_PROVISORIA
    aprovado = verificar_numeros(texto, permitidos)
    registrar(
        sessao,
        conversa.id,
        "verificacao",
        "numeros",
        dados={
            "permitidos": sorted(str(p) for p in permitidos),
            "veredito": "aprovado" if aprovado else "reprovado",
        },
    )

    if not aprovado:
        # S-03 §4 passo 3: bloqueia e transfere. O texto reprovado não é gravado, não é
        # fatiado e não aparece no evento de erro — nem parcialmente.
        conversa.modo = "humano"
        conversa.etapa = "humano"
        entrada.processada_em = agora()
        sessao.commit()
        registrar_incidente(sessao, conversa.id, "numero_divergente", {"etapa": etapa_inicial})
        yield "erro", {"codigo": "numero_divergente", "mensagem_ao_cliente": PEDIDO_DESCULPA}
        return

    saida = _gravar_saida(sessao, conversa, entrada, texto, da_ia=True)
    conversa.etapa = _proxima_etapa(conversa.etapa)
    sessao.commit()

    registrar(
        sessao,
        conversa.id,
        "turno",
        "aurora",
        dados={"etapa": etapa_inicial, "tools": list(tools), "versao_do_prompt": None},
        duracao_ms=_ms(comeco),
        # ponytail: zero até a S-03 — o custo é o `usage` que o provedor devolve, e não
        # há chamada ainda. Estimar por tokenizer local seria inventar o número que o
        # Raí paga, que é exatamente o que o ADR-006 recusa.
        custo_micro_reais=0,
    )
    marcar_se_conversa_cara(sessao, conversa.id)

    for pedaco in _fatiar(texto):
        yield "token", {"texto": pedaco}
    yield "mensagem_fim", {"mensagem_id": str(saida.id), "etapa": conversa.etapa}
