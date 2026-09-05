"""S-01 §2 e §3 — o que a landing aceita como nome e como telefone.

Puro, sem banco e sem FastAPI: é a regra de negócio, e ela é testável sozinha.
"""

import re

MSG_SO_CELULAR = "Precisa ser um celular com WhatsApp — é por lá que a gente continua a conversa."
MSG_DDD = "Esse DDD não existe no Brasil. Confere o número?"
MSG_NOME_TAMANHO = "O nome precisa ter entre 2 e 80 caracteres."
MSG_NOME_CARACTERE = "Use só letras, espaço, apóstrofo e hífen no nome."

# DDDs em uso no Brasil. Faltam de propósito os nunca atribuídos (20, 23, 25, 26, 29, 30, 36…).
DDDS_VALIDOS = frozenset(
    {
        "11", "12", "13", "14", "15", "16", "17", "18", "19",
        "21", "22", "24", "27", "28",
        "31", "32", "33", "34", "35", "37", "38",
        "41", "42", "43", "44", "45", "46", "47", "48", "49",
        "51", "53", "54", "55",
        "61", "62", "63", "64", "65", "66", "67", "68", "69",
        "71", "73", "74", "75", "77", "79",
        "81", "82", "83", "84", "85", "86", "87", "88", "89",
        "91", "92", "93", "94", "95", "96", "97", "98", "99",
    }
)  # fmt: skip

_NOME_ACEITO = re.compile(r"^[^\W\d_](?:[^\W\d_]|[ '\-])*$")


def normalizar_nome(bruto: str) -> str:
    """`strip`, colapsa espaço interno e **não** força capitalização (S-01 §2)."""
    nome = " ".join(bruto.split())
    if not 2 <= len(nome) <= 80:
        raise ValueError(MSG_NOME_TAMANHO)
    if not _NOME_ACEITO.match(nome):
        raise ValueError(MSG_NOME_CARACTERE)
    return nome


def normalizar_telefone(bruto: str) -> str:
    """Celular brasileiro em E.164. Fixo e nono dígito diferente de 9 são recusados."""
    digitos = re.sub(r"\D", "", bruto)
    if len(digitos) == 13 and digitos.startswith("55"):
        digitos = digitos[2:]
    if len(digitos) != 11:
        raise ValueError(MSG_SO_CELULAR)
    if digitos[:2] not in DDDS_VALIDOS:
        raise ValueError(MSG_DDD)
    if digitos[2] != "9":
        raise ValueError(MSG_SO_CELULAR)
    return f"+55{digitos}"


def primeiro_nome(nome: str) -> str:
    """O vocativo da Aurora. É o único pedaço do nome que entra no prompt (S-09 §4)."""
    return nome.split()[0]
