"""O registro de tools: esquema para o modelo, despacho para o código.

Duas regras moram aqui, e as duas são de segurança e não de organização:

1. **A etapa filtra a execução, não só a oferta.** Não basta deixar de mostrar a tool ao
   modelo — se ele inventar o nome, o despacho recusa. Oferta é o que o modelo vê;
   execução é o que o código permite ([S-02 §2](../../../docs/spec/S-02-chat-web-e-sessao.md)).
2. **Tool que não existe é a garantia** (ADR-004). `aplicar_desconto`, `alterar_preco` e
   `criar_condicao_especial` não estão aqui, e a ausência é o mecanismo: uma mensagem que
   peça 30% de desconto não tem função para chamar.
"""

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.ia.etapas import tools_da_etapa
from app.ia.tools.estoque import buscar_unidades, comparar_unidades, detalhar_unidade
from app.ia.tools.qualificacao import registrar_qualificacao, transferir_para_humano
from app.modelos import Conversa


@dataclass(frozen=True)
class Tool:
    nome: str
    descricao: str
    parametros: dict[str, object]
    executar: Callable[[Session, Conversa, dict[str, object]], object]


def _lista(bruto: object) -> list[str]:
    """O modelo pode mandar string em vez de array. Uma só não compara com nada, e a
    `comparar_unidades` recusa — que é o comportamento certo, com erro legível."""
    if isinstance(bruto, str):
        return [bruto]
    return [str(item) for item in bruto] if isinstance(bruto, list) else []


def _objeto(propriedades: dict[str, object], obrigatorios: list[str]) -> dict[str, object]:
    return {
        "type": "object",
        "properties": propriedades,
        "required": obrigatorios,
        "additionalProperties": False,
    }


REGISTRO: dict[str, Tool] = {
    "buscar_unidades": Tool(
        nome="buscar_unidades",
        descricao=(
            "Lista os carros disponíveis no pátio da Sol & Volt. Use sempre antes de "
            "falar de preço, modelo ou disponibilidade. Devolve só o que está à venda."
        ),
        parametros=_objeto(
            {
                "preco_max_centavos": {
                    "type": "integer",
                    "description": "Teto de preço em centavos. R$ 150.000 é 15000000.",
                },
                "condicao": {"type": "string", "enum": ["novo", "seminovo"]},
                "autonomia_min_km": {"type": "integer"},
            },
            [],
        ),
        executar=lambda sessao, _conversa, args: buscar_unidades(
            sessao,
            preco_max_centavos=args.get("preco_max_centavos"),  # type: ignore[arg-type]
            condicao=args.get("condicao"),  # type: ignore[arg-type]
            autonomia_min_km=args.get("autonomia_min_km"),  # type: ignore[arg-type]
        ),
    ),
    "detalhar_unidade": Tool(
        nome="detalhar_unidade",
        descricao="Ficha completa de um chassi. Use antes de falar do preço de um carro.",
        parametros=_objeto({"chassi": {"type": "string"}}, ["chassi"]),
        executar=lambda sessao, _conversa, args: detalhar_unidade(sessao, str(args["chassi"])),
    ),
    "comparar_unidades": Tool(
        nome="comparar_unidades",
        descricao=(
            "Compara 2 ou 3 chassis lado a lado. Respeite o campo 'aviso' do retorno: "
            "quando ele vier preenchido, apresente os dois números com a fonte de cada um "
            "e NÃO diga qual roda mais."
        ),
        parametros=_objeto(
            {
                "chassis": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 2,
                    "maxItems": 3,
                }
            },
            ["chassis"],
        ),
        executar=lambda sessao, _conversa, args: comparar_unidades(sessao, _lista(args["chassis"])),
    ),
    "registrar_qualificacao": Tool(
        nome="registrar_qualificacao",
        descricao="Anota o que o cliente já contou sobre o uso do carro. Só o que ele disse.",
        parametros=_objeto(
            {
                "uso": {"type": "string"},
                "km_dia": {"type": "integer"},
                "orcamento_max_centavos": {"type": "integer"},
                "cidade": {"type": "string"},
                "tem_carregador": {"type": "boolean"},
                "prazo_compra": {"type": "string"},
            },
            [],
        ),
        executar=lambda sessao, conversa, args: registrar_qualificacao(sessao, conversa, **args),
    ),
    "transferir_para_humano": Tool(
        nome="transferir_para_humano",
        descricao=(
            "Passa a conversa para Tarcísio ou Jaqueline. Use quando o cliente pedir "
            "pessoa, quando pedir desconto ou condição especial, ou quando você não "
            "tiver como responder com número que veio de tool."
        ),
        parametros=_objeto({"motivo": {"type": "string"}}, ["motivo"]),
        executar=lambda sessao, conversa, args: transferir_para_humano(
            sessao, conversa, motivo=str(args.get("motivo", ""))
        ),
    ),
}


def disponiveis(etapa: str) -> tuple[str, ...]:
    """A interseção entre o que a etapa permite e o que existe implementado.

    `tools_da_etapa` continua sendo a autoridade sobre o que é permitido — a interseção
    só deixa de oferecer o que ainda não foi escrito. Ampliar poder continua exigindo
    editar `etapas.py`, que é arquivo de revisão humana obrigatória.
    """
    return tuple(nome for nome in tools_da_etapa(etapa) if nome in REGISTRO)


def esquemas(etapa: str) -> list[dict[str, object]]:
    return [
        {
            "type": "function",
            "function": {
                "name": REGISTRO[nome].nome,
                "description": REGISTRO[nome].descricao,
                "parameters": REGISTRO[nome].parametros,
            },
        }
        for nome in disponiveis(etapa)
    ]


def executar(
    sessao: Session, conversa: Conversa, nome: str, argumentos: dict[str, object]
) -> object:
    """Recusa nome que a etapa não permite, mesmo que o modelo o tenha inventado."""
    if nome not in disponiveis(conversa.etapa):
        raise PermissionError(f"tool {nome} não existe na etapa {conversa.etapa}")
    return REGISTRO[nome].executar(sessao, conversa, argumentos)
