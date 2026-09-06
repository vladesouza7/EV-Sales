"""Reais em algarismos e por extenso, para o Espelho (S-04 §5).

O valor por extenso não é enfeite de documento: é o que impede que um algarismo alterado
passe despercebido. Por isso o PDF traz os dois, e por isso isto é código testado e não
uma linha de formatação perdida no gerador.

Centavos entram como `int`, sempre — dinheiro não é ponto flutuante (CLAUDE.md).
"""

_UNIDADES = (
    "", "um", "dois", "três", "quatro", "cinco", "seis", "sete", "oito", "nove",
    "dez", "onze", "doze", "treze", "quatorze", "quinze", "dezesseis", "dezessete",
    "dezoito", "dezenove",
)  # fmt: skip
_DEZENAS = (
    "", "", "vinte", "trinta", "quarenta", "cinquenta", "sessenta", "setenta",
    "oitenta", "noventa",
)  # fmt: skip
_CENTENAS = (
    "", "cento", "duzentos", "trezentos", "quatrocentos", "quinhentos", "seiscentos",
    "setecentos", "oitocentos", "novecentos",
)  # fmt: skip


def formatar(centavos: int) -> str:
    """`24999000` → `R$ 249.990,00`."""
    inteiro, resto = divmod(abs(centavos), 100)
    sinal = "-" if centavos < 0 else ""
    return f"{sinal}R$ {inteiro:,.0f}".replace(",", ".") + f",{resto:02d}"


def _ate_999(numero: int) -> str:
    if numero == 100:
        return "cem"  # "cento" só existe acompanhado: cento e um, cento e dez
    centena, resto = divmod(numero, 100)
    partes = [_CENTENAS[centena]] if centena else []
    if resto < 20:
        partes += [_UNIDADES[resto]] if resto else []
    else:
        dezena, unidade = divmod(resto, 10)
        partes.append(_DEZENAS[dezena] + (f" e {_UNIDADES[unidade]}" if unidade else ""))
    return " e ".join(p for p in partes if p)


def _juntar(blocos: list[tuple[int, str]]) -> str:
    """A regra clássica da escrita de valor: o "e" liga o último grupo quando ele é menor
    que 100 ou múltiplo de 100; nos demais casos entra vírgula.

    "dois milhões E quinhentos mil reais", mas "duzentos e quarenta e nove mil, novecentos
    e noventa reais". É a forma usada em documento de valor, e é por isso que a regra não
    é simplesmente juntar tudo com vírgula.
    """
    if len(blocos) == 1:
        return blocos[0][1]
    valor_final, texto_final = blocos[-1]
    anteriores = ", ".join(texto for _, texto in blocos[:-1])
    ligacao = " e " if valor_final < 100 or valor_final % 100 == 0 else ", "
    return f"{anteriores}{ligacao}{texto_final}"


def por_extenso(centavos: int) -> str:
    """`24999000` → `duzentos e quarenta e nove mil, novecentos e noventa reais`.

    Vai até milhões, que é o teto realista de um carro na Sol & Volt. Acima disso o
    resultado seria errado em silêncio, então a função recusa — documento com valor
    errado por extenso é pior que documento sem valor por extenso.
    """
    if centavos < 0:
        raise ValueError("O Espelho não carrega valor negativo.")
    reais, centavos_restantes = divmod(centavos, 100)
    if reais >= 1_000_000_000:
        raise ValueError("Valor acima do que esta função sabe escrever.")

    if reais == 0:
        texto = ""
    else:
        milhoes, resto = divmod(reais, 1_000_000)
        milhares, unidades = divmod(resto, 1_000)
        blocos: list[tuple[int, str]] = []
        if milhoes:
            nome = "milhão" if milhoes == 1 else "milhões"
            blocos.append((milhoes * 1_000_000, f"{_ate_999(milhoes)} {nome}"))
        if milhares:
            escrito = f"{_ate_999(milhares)} mil" if milhares > 1 else "mil"
            blocos.append((milhares * 1_000, escrito))
        if unidades:
            blocos.append((unidades, _ate_999(unidades)))
        texto = _juntar(blocos)
        if reais == 1:
            texto += " real"
        elif milhoes and resto == 0:
            # "um milhão DE reais", mas "um milhão e quinhentos mil reais": o "de" só
            # aparece quando o milhão não é seguido de nada.
            texto += " de reais"
        else:
            texto += " reais"

    if centavos_restantes:
        centavos_texto = f"{_ate_999(centavos_restantes)} "
        centavos_texto += "centavo" if centavos_restantes == 1 else "centavos"
        return f"{texto} e {centavos_texto}" if texto else centavos_texto
    return texto or "zero real"
