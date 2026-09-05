"""S-02 §2 — a etapa decide quais tools existem no turno.

REVISÃO HUMANA OBRIGATÓRIA (CLAUDE.md): este mapa é a superfície do que a Aurora pode
fazer. Acrescentar uma tool a uma etapa é ampliar poder, não configurar comportamento —
e ampliar poder por edição de dicionário é exatamente o que a tabela do CLAUDE.md
manda passar por revisão.

A transição de etapa vem do retorno de uma tool ou de regra de código (ADR-009). O
modelo nunca declara que mudou de etapa: se pudesse, a lista de tools do turno seguinte
seria escolhida por ele.
"""

TOOLS_POR_ETAPA: dict[str, tuple[str, ...]] = {
    "saudacao": (),
    "qualificacao": ("registrar_qualificacao", "buscar_conhecimento"),
    "recomendacao": (
        "buscar_unidades",
        "detalhar_unidade",
        "comparar_unidades",
        "buscar_conhecimento",
    ),
    "objecao": (
        "buscar_unidades",
        "detalhar_unidade",
        "comparar_unidades",
        "buscar_conhecimento",
        "calcular_custo_km",
    ),
    "condicao": ("solicitar_aprovacao",),
    "aguardando_aprovacao": (),
    "reserva": ("reservar_chassi",),
    "test_drive": ("consultar_agenda", "agendar_test_drive"),
    "humano": (),
    "encerrada": (),
}

# `transferir_para_humano` existe em toda etapa que ainda tem turno da Aurora. Em
# `aguardando_aprovacao` não: a S-02 §2 marca **nenhuma** e o critério de aceite exige
# lista vazia — parado esperando a Neuza, o turno não tem o que chamar.
SEM_TRANSFERENCIA = ("aguardando_aprovacao", "humano", "encerrada")


def tools_da_etapa(etapa: str) -> tuple[str, ...]:
    disponiveis = TOOLS_POR_ETAPA[etapa]
    if etapa in SEM_TRANSFERENCIA:
        return disponiveis
    return disponiveis + ("transferir_para_humano",)
