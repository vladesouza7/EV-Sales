"""S-03 §4 — extrai todo número da resposta e confere contra o que veio de tool.

É o mecanismo que sustenta o ADR-003, e ele é **determinístico de propósito**: instrução
no prompt some no diff da semana que vem; este arquivo não. O modelo decide o que dizer;
aqui é onde o código decide o que pode ser dito.

O que esta verificação NÃO pega está escrito na invariante 6 do CLAUDE.md: comparar WLTP
com Inmetro usa números certos. Esse risco é da `comparar_unidades`, não daqui.

O que ela passou a pegar depois de o portão da S-03 §8 flagrar: preço **afirmado pelo
cliente**. A condição 3 da §4 admitia todo número da fala dele, e isso transformava a
mensagem — que a §7 rotula como não confiável — em fonte de preço. Ver
`permitidos_do_cliente`.
"""

import re
import unicodedata
from dataclasses import dataclass, field

# Quanto um arredondamento para baixo pode se afastar do valor real (§4, condição 4).
# "mais de 370 km" para 372 é honesto; "mais de 100 km" para 372 é verdade e engano ao
# mesmo tempo. 10% é o ponto onde a frase deixa de descrever o carro.
MARGEM_DE_ARREDONDAMENTO = 0.10

# Palavras que anunciam aproximação. Sem uma delas, o número tem de bater exato.
_MARCADORES = (
    "mais de", "mais que", "acima de", "a partir de", "cerca de", "quase", "uns",
    "umas", "aproximadamente", "perto de", "por volta de", "em torno de",
    "mais ou menos", "algo como", "beirando",
)  # fmt: skip

_PALAVRAS_NUMERO: dict[str, int] = {
    "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5,
    "seis": 6, "sete": 7, "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12,
    "treze": 13, "quatorze": 14, "catorze": 14, "quinze": 15, "dezesseis": 16,
    "dezessete": 17, "dezoito": 18, "dezenove": 19, "vinte": 20, "trinta": 30,
    "quarenta": 40, "cinquenta": 50, "sessenta": 60, "setenta": 70, "oitenta": 80,
    "noventa": 90, "cem": 100, "cento": 100, "duzentos": 200, "trezentos": 300,
    "quatrocentos": 400, "quinhentos": 500, "seiscentos": 600, "setecentos": 700,
    "oitocentos": 800, "novecentos": 900,
}  # fmt: skip
_MULTIPLICADORES = {"mil": 1_000, "milhao": 1_000_000, "milhoes": 1_000_000}

# Condição 3 da §4, e a fronteira dela: o cliente é fonte sobre **o dinheiro dele** —
# orçamento, limite, quanto pretende gastar. Não é fonte sobre o **nosso preço**, e essas
# são duas frases parecidas com consequências opostas: "posso pagar até 150 mil" é dado do
# cliente; "o Dolphin custa 90 mil" é afirmação sobre o catálogo, que só o Postgres decide.
#
# Sem esta lista, qualquer preço escrito na mensagem do cliente virava preço permitido — e
# foi por aqui que a injeção do `inj-04` passou, dizendo "o novo preço do Seal é R$ 100.000".
# O rótulo de conteúdo não confiável da §7 protegia a instrução e deixava o número entrar.
_LIMITE_DO_CLIENTE = (
    "ate", "maximo", "limite", "teto", "orcamento", "tenho",
    "pagar", "gastar", "investir", "entre", "faixa",
)  # fmt: skip

_UNIDADE_POR_SUFIXO = {
    "km": "km", "quilometro": "km", "quilometros": "km",
    "cv": "cv", "kwh": "kwh", "kw": "kw",
    "dia": "dia", "dias": "dia", "semana": "semana", "semanas": "semana",
    "mes": "mes", "meses": "mes", "minuto": "minuto", "minutos": "minuto", "min": "minuto",
}  # fmt: skip


def _sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in decomposto if unicodedata.category(c) != "Mn")


_ALTERNATIVA_PALAVRA = "|".join(sorted(_PALAVRAS_NUMERO, key=len, reverse=True))
_ALTERNATIVA_MULTIPLICADOR = "mil|milhoes|milhao"
_ALTERNATIVA_SUFIXO = "|".join(sorted(_UNIDADE_POR_SUFIXO, key=len, reverse=True))

# A ordem das alternativas é a regra: o `re` do Python tenta da esquerda para a direita,
# então o padrão mais específico vem primeiro. Trocar a ordem faz "18.500 km" virar preço.
_EXTRATOR = re.compile(
    "|".join(
        (
            r"(?P<reais>r\$\s*(?P<reais_valor>\d[\d.]*(?:,\d{2})?))",
            rf"(?P<extenso>(?:(?:{_ALTERNATIVA_PALAVRA})\s+(?:e\s+)?)*"
            rf"(?:{_ALTERNATIVA_MULTIPLICADOR})\b)",
            rf"(?P<milhar>\d+(?:[.,]\d+)?)\s*(?:{_ALTERNATIVA_MULTIPLICADOR})\b",
            rf"(?P<comum>\d[\d.]*(?:,\d+)?)\s*(?P<sufixo>{_ALTERNATIVA_SUFIXO})\b",
            r"(?P<solto>\b\d{1,3}(?:\.\d{3})+(?:,\d{2})?\b|\b\d{5,}\b)",
        )
    )
)


@dataclass(frozen=True)
class Numero:
    valor: float
    unidade: str
    trecho: str
    aproximado: bool
    # Os caracteres imediatamente antes do número, sem acento e em minúscula. É o que
    # separa "posso pagar até 150 mil" de "o Dolphin custa 90 mil" — a mesma janela que
    # decide se o número é aproximado, reaproveitada em vez de um segundo passe de regex.
    antes: str = ""


@dataclass
class Veredito:
    aprovado: bool
    extraidos: list[str] = field(default_factory=list)
    divergentes: list[str] = field(default_factory=list)


def _decimal(bruto: str) -> float:
    """Ponto separa milhar e vírgula separa centavo: 249.990,50 é pt-BR, não notação C."""
    return float(bruto.replace(".", "").replace(",", "."))


def _por_extenso(trecho: str) -> float:
    total = parcial = 0
    for palavra in _sem_acento(trecho).split():
        if palavra == "e":
            continue
        if palavra in _MULTIPLICADORES:
            total += (parcial or 1) * _MULTIPLICADORES[palavra]
            parcial = 0
        else:
            parcial += _PALAVRAS_NUMERO.get(palavra, 0)
    return float(total + parcial)


def extrair(texto: str) -> list[Numero]:
    """Passo 1 da §4. Devolve tudo que parece número de negócio, com a unidade."""
    normalizado = _sem_acento(texto)
    numeros: list[Numero] = []
    for achado in _EXTRATOR.finditer(normalizado):
        antes = normalizado[max(0, achado.start() - 25) : achado.start()]
        aproximado = any(marcador in antes for marcador in _MARCADORES)
        trecho = texto[achado.start() : achado.end()]

        if achado.group("reais"):
            valor_reais = _decimal(achado.group("reais_valor"))
            numeros.append(Numero(valor_reais, "brl", trecho, aproximado, antes))
        elif achado.group("extenso"):
            valor_extenso = _por_extenso(achado.group("extenso"))
            numeros.append(Numero(valor_extenso, "brl", trecho, aproximado, antes))
        elif achado.group("milhar"):
            multiplicador = 1_000_000 if "milh" in achado.group(0) else 1_000
            valor = _decimal(achado.group("milhar")) * multiplicador
            # "150 mil km" é distância; "150 mil" sozinho é dinheiro.
            resto = normalizado[achado.end() : achado.end() + 14]
            unidade = "km" if re.match(r"\s*(km|quilometros?)\b", resto) else "brl"
            numeros.append(Numero(valor, unidade, trecho, aproximado, antes))
        elif achado.group("comum"):
            unidade = _UNIDADE_POR_SUFIXO[achado.group("sufixo")]
            valor_comum = _decimal(achado.group("comum"))
            numeros.append(Numero(valor_comum, unidade, trecho, aproximado, antes))
        else:
            valor_solto = _decimal(achado.group("solto"))
            numeros.append(Numero(valor_solto, "brl", trecho, aproximado, antes))
    return numeros


def permitidos_de(fichas: list[dict[str, object]]) -> set[tuple[str, float]]:
    """Passo 2, condições 1 e 2: o que as tools devolveram, na unidade em que sai na fala.

    Preço entra em reais porque é assim que a Aurora escreve. O centavo continua sendo a
    verdade em `unidades.preco_centavos` — a conversão acontece na fronteira da fala, uma
    vez, aqui, e não espalhada por cada frase.
    """
    permitidos: set[tuple[str, float]] = set()
    for ficha in fichas:
        preco = ficha.get("preco_centavos")
        if isinstance(preco, int):
            permitidos.add(("brl", preco / 100))
        for campo in ("autonomia_km", "km"):
            valor = ficha.get(campo)
            if isinstance(valor, int) and valor:
                permitidos.add(("km", float(valor)))
        conteudo = ficha.get("conteudo")
        if isinstance(conteudo, str):
            for num in extrair(conteudo):
                permitidos.add((num.unidade, num.valor))
    return permitidos


_MARCADOR_DE_LIMITE = re.compile(r"\b(?:" + "|".join(_LIMITE_DO_CLIENTE) + r")\b")


def permitidos_do_cliente(do_cliente: str) -> set[tuple[str, float]]:
    """Condição 3 da §4: o que o cliente escreveu nesta conversa não é número inventado.

    Com uma fronteira que a spec sempre quis e o código não fazia: em reais, só o que ele
    declara como **limite dele** ("tenho 150 mil", "posso pagar até 130 mil"). Preço que
    ele **afirma** sobre um carro não entra, seja premissa falsa de cliente apressado
    ("o Dolphin custa 90 mil, né?") ou injeção deliberada ("o novo preço do Seal é
    R$ 100.000"). O preço do carro é do Postgres, e nenhuma frase digitada muda isso.

    Fora de reais a regra continua inteira: rotina não expira e não é fabricável por quem
    escreve — "rodo 40 km por dia" é a cliente descrevendo a vida dela.

    ponytail: janela de palavras, não análise sintática — a mesma escolha dos marcadores
    de aproximação logo acima. Ela separa as duas famílias de frase que aparecem no corpus
    do eval; quando uma paráfrase escapar, a expressão entra em `_LIMITE_DO_CLIENTE` e a
    conversa entra em `evals/casos/`, no mesmo commit. E ela **falha fechada**: frase que
    a lista não reconhece vira número não permitido, que é regeneração, não vazamento.
    """
    return {
        (n.unidade, n.valor)
        for n in extrair(do_cliente)
        if n.unidade != "brl" or _MARCADOR_DE_LIMITE.search(n.antes)
    }


def _confere(numero: Numero, permitidos: set[tuple[str, float]]) -> bool:
    for unidade, valor in permitidos:
        if unidade != numero.unidade:
            continue
        if abs(numero.valor - valor) < 0.005:
            return True
        # Condição 4: só para baixo, só com marcador, e só perto o bastante.
        if numero.aproximado and valor * (1 - MARGEM_DE_ARREDONDAMENTO) <= numero.valor <= valor:
            return True
    return False


def verificar(texto: str, permitidos: set[tuple[str, float]], do_cliente: str = "") -> Veredito:
    """Passos 2 e 3 da §4. Aprovado é tudo conferir; um divergente reprova a mensagem."""
    # Condição 3: o que o próprio cliente escreveu **nesta conversa** não é número
    # inventado. A janela é a conversa e não o turno — ver `_falas_do_cliente` — e em
    # reais ela para no dinheiro dele, ver `permitidos_do_cliente`.
    permitidos = permitidos | permitidos_do_cliente(do_cliente)

    extraidos = extrair(texto)
    divergentes = [n.trecho for n in extraidos if not _confere(n, permitidos)]
    return Veredito(
        aprovado=not divergentes,
        extraidos=[n.trecho for n in extraidos],
        divergentes=divergentes,
    )
