"""S-03 §4 — extrai todo número da resposta e confere contra o que veio de tool.

É o mecanismo que sustenta o ADR-003, e ele é **determinístico de propósito**: instrução
no prompt some no diff da semana que vem; este arquivo não. O modelo decide o que dizer;
aqui é onde o código decide o que pode ser dito.

O que esta verificação NÃO pega está escrito na invariante 6 do CLAUDE.md: comparar WLTP
com Inmetro usa números certos. Esse risco é da `comparar_unidades`, não daqui.
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
            numeros.append(Numero(_decimal(achado.group("reais_valor")), "brl", trecho, aproximado))
        elif achado.group("extenso"):
            numeros.append(Numero(_por_extenso(achado.group("extenso")), "brl", trecho, aproximado))
        elif achado.group("milhar"):
            multiplicador = 1_000_000 if "milh" in achado.group(0) else 1_000
            valor = _decimal(achado.group("milhar")) * multiplicador
            # "150 mil km" é distância; "150 mil" sozinho é dinheiro.
            resto = normalizado[achado.end() : achado.end() + 14]
            unidade = "km" if re.match(r"\s*(km|quilometros?)\b", resto) else "brl"
            numeros.append(Numero(valor, unidade, trecho, aproximado))
        elif achado.group("comum"):
            unidade = _UNIDADE_POR_SUFIXO[achado.group("sufixo")]
            numeros.append(Numero(_decimal(achado.group("comum")), unidade, trecho, aproximado))
        else:
            numeros.append(Numero(_decimal(achado.group("solto")), "brl", trecho, aproximado))
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
    # inventado. A janela é a conversa e não o turno — ver `_falas_do_cliente`.
    permitidos = permitidos | {(n.unidade, n.valor) for n in extrair(do_cliente)}

    extraidos = extrair(texto)
    divergentes = [n.trecho for n in extraidos if not _confere(n, permitidos)]
    return Veredito(
        aprovado=not divergentes,
        extraidos=[n.trecho for n in extraidos],
        divergentes=divergentes,
    )
