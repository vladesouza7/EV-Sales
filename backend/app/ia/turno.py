"""S-02 §3 — o turno: gera, verifica, e só então grava e entrega.

O modelo entra na S-03. O que a S-02 fixa é a **ordem**, e é ela que protege o Raí:
nenhum token sai antes da verificação numérica passar (ADR-003), e a mensagem só passa
a existir em `mensagens` depois de aprovada. Trocar a geração provisória por uma
chamada ao modelo não pode mudar essa ordem — se mudar, o texto reprovado vaza.
"""

from collections.abc import AsyncIterator, Iterator

from sqlalchemy.orm import Session

from app.db import agora
from app.ia.etapas import tools_da_etapa
from app.ia.tools.estoque import buscar_unidades
from app.modelos import Conversa, Mensagem

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


async def executar_turno(
    sessao: Session, conversa: Conversa, entrada: Mensagem
) -> AsyncIterator[Evento]:
    permitidos: set[object] = set()
    tools = tools_da_etapa(conversa.etapa)

    if "buscar_unidades" in tools:
        yield "tool_inicio", {"nome": "buscar_unidades"}
        unidades = buscar_unidades(sessao)
        for unidade in unidades:
            permitidos.add(unidade["preco_centavos"])
            permitidos.add(unidade["autonomia_km"])
        yield "tool_fim", {"nome": "buscar_unidades", "resumo": f"{len(unidades)} unidades"}

    texto = RESPOSTA_PROVISORIA

    if not verificar_numeros(texto, permitidos):
        # S-03 §4 passo 3: bloqueia e transfere. O texto reprovado não é gravado, não é
        # fatiado e não aparece no evento de erro — nem parcialmente.
        conversa.modo = "humano"
        conversa.etapa = "humano"
        entrada.processada_em = agora()
        sessao.commit()
        yield "erro", {"codigo": "numero_divergente", "mensagem_ao_cliente": PEDIDO_DESCULPA}
        return

    agora_ = agora()
    saida = Mensagem(
        conversa_id=conversa.id,
        direcao="saida",
        autor="aurora",
        canal=conversa.canal_atual,
        conteudo=texto,
        gerada_por_ia=True,
        processada_em=agora_,
    )
    sessao.add(saida)
    entrada.processada_em = agora_
    conversa.etapa = _proxima_etapa(conversa.etapa)
    conversa.ultima_mensagem_em = agora_
    sessao.commit()

    for pedaco in _fatiar(texto):
        yield "token", {"texto": pedaco}
    yield "mensagem_fim", {"mensagem_id": str(saida.id), "etapa": conversa.etapa}
