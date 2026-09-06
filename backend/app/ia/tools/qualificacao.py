"""Tools de escrita na conversa (S-03 §2 e §3).

As duas escrevem, e nenhuma das duas toca em preço, estoque ou reserva. É a diferença
entre "a Aurora anota o que entendeu" e "a Aurora muda o que a loja vende".
"""

from sqlalchemy.orm import Session

from app.modelos import Conversa

# S-03 §3 — o que a qualificação tenta preencher, quando fizer sentido. Campo fora desta
# lista é ignorado: a tool é a fronteira do que o modelo pode gravar, e um `**kwargs`
# aberto deixaria o modelo inventar campo dentro do jsonb.
CAMPOS = (
    "uso",
    "km_dia",
    "orcamento_max_centavos",
    "cidade",
    "tem_carregador",
    "prazo_compra",
)


def registrar_qualificacao(
    sessao: Session, conversa: Conversa, **campos: object
) -> dict[str, object]:
    """Acumula. O turno 7 não apaga o que o cliente respondeu no turno 2.

    A reatribuição do dicionário é proposital: `qualificacao` é JSONB, e mutar em memória
    não marca o objeto como sujo — a gravação sumiria em silêncio no commit.
    """
    novos = {c: v for c, v in campos.items() if c in CAMPOS and v is not None}
    conversa.qualificacao = {**conversa.qualificacao, **novos}
    sessao.commit()
    return dict(conversa.qualificacao)


def transferir_para_humano(
    sessao: Session, conversa: Conversa, motivo: str = ""
) -> dict[str, object]:
    """S-02 §5 — a mesma coisa que o botão da tela faz, pelo lado do agente."""
    conversa.modo = "humano"
    conversa.etapa = "humano"
    sessao.commit()
    return {"modo": conversa.modo, "etapa": conversa.etapa, "motivo": motivo}
