"""Dublê do provedor de LLM.

O turno inteiro é testável sem rede e sem chave: a ordem que a S-02 §3 fixa e a
verificação da S-03 §4 são código nosso, e código nosso se testa sem pedir licença ao
OpenRouter. O que a suíte **não** cobre por aqui é a qualidade da resposta do modelo —
isso é o eval da S-03 §8, que roda contra o provedor de verdade.
"""

from app.ia.provedor import ChamadaDeTool, Resposta

# Sem número nenhum de propósito: texto sem número é o único texto que a verificação
# aprova em qualquer estoque.
TEXTO_PADRAO = (
    "Oi! Sou a Aurora, consultora da Sol & Volt, aqui em Tambaú. "
    "Me conta como você usa o carro no dia a dia."
)


class ProvedorDuble:
    """Devolve as respostas na ordem em que foram programadas; depois, o texto padrão."""

    nome = "duble"

    def __init__(self) -> None:
        self.respostas: list[Resposta] = []
        self.chamadas: list[tuple[list[dict[str, object]], list[dict[str, object]]]] = []
        self.disponivel = True

    def responder(
        self, texto: str, custo_micro_reais: int = 0, custo_faturado: bool = True
    ) -> None:
        self.respostas.append(
            Resposta(
                texto=texto,
                modelo="duble/teste",
                custo_micro_reais=custo_micro_reais,
                custo_faturado=custo_faturado,
            )
        )

    def chamar_tool(self, nome: str, **argumentos: object) -> None:
        self.respostas.append(
            Resposta(
                texto="",
                modelo="duble/teste",
                custo_micro_reais=0,
                tools=[ChamadaDeTool(id=f"call_{nome}", nome=nome, argumentos=argumentos)],
            )
        )

    def configurado(self) -> bool:
        return self.disponivel

    async def conversar(
        self, mensagens: list[dict[str, object]], tools: list[dict[str, object]]
    ) -> Resposta:
        self.chamadas.append((mensagens, tools))
        if self.respostas:
            return self.respostas.pop(0)
        return Resposta(texto=TEXTO_PADRAO, modelo="duble/teste", custo_micro_reais=0)
