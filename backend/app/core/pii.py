"""S-09 / ADR-007 — cifragem em repouso, hash indexável e mascaramento na origem.

ARQUIVO DE REVISÃO HUMANA OBRIGATÓRIA (CLAUDE.md). Mudança aqui muda a proteção do
ativo que o Raí herdou do pai.

Decifrar é chamada explícita, e a spec autoriza exatamente três chamadores:
link `wa.me`, envio pela Evolution API e tela do vendedor autenticado.
"""

import base64
import hashlib
import hmac
import os
import re

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_TAMANHO_NONCE = 12


def _segredo(variavel: str) -> bytes:
    bruto = os.environ.get(variavel)
    if not bruto:
        raise RuntimeError(f"{variavel} não está no ambiente. Rode scripts/gerar-segredos.sh.")
    segredo = base64.b64decode(bruto)
    if len(segredo) != 32:
        raise RuntimeError(f"{variavel} precisa ter 32 bytes em base64.")
    return segredo


def cifrar(texto: str) -> bytes:
    """AES-256-GCM com nonce por registro; o nonce viaja no começo do blob."""
    nonce = os.urandom(_TAMANHO_NONCE)
    return nonce + AESGCM(_segredo("EVSALES_PII_KEY")).encrypt(nonce, texto.encode(), None)


def decifrar(blob: bytes) -> str:
    chave = AESGCM(_segredo("EVSALES_PII_KEY"))
    return chave.decrypt(blob[:_TAMANHO_NONCE], blob[_TAMANHO_NONCE:], None).decode()


def hash_telefone(e164: str) -> str:
    """Determinístico e indexável: reconhece o cliente que volta sem decifrar nada."""
    return hmac.new(_segredo("EVSALES_PII_PEPPER"), e164.encode(), hashlib.sha256).hexdigest()


def mascarar_telefone(e164: str) -> str:
    """`+5583988714471` → `(83) *****-4471`.

    Entrada curta demais não vira máscara inventada: `3244-1010` não tem DDD, e
    devolver `(32) *****-1010` seria a máscara mentindo sobre o número.
    """
    digitos = re.sub(r"\D", "", e164)
    if len(digitos) < 10:
        return "[TELEFONE-REMOVIDO]"
    digitos = digitos[-11:] if len(digitos) >= 11 else digitos[-10:]
    return f"({digitos[:2]}) *****-{digitos[-4:]}"


def mascarar_nome(nome: str) -> str:
    partes = nome.split()
    if not partes:
        return "[NOME-REMOVIDO]"
    return partes[0] if len(partes) == 1 else f"{partes[0]} {partes[-1][0]}."


# Um celular colado com DDD (11 dígitos) e um CPF sem pontuação têm o mesmo tamanho.
# Numa concessionária o cliente digita o telefone muito mais vezes do que o CPF, então
# `\d{2}9\d{8}` é lido como telefone. Os dois são redigidos; só o rótulo difere.
_TELEFONE = "|".join(
    (
        r"\+?55[\s.\-]?\(?\d{2}\)?[\s.\-]?9?[\s.\-]?\d{4}[\s.\-]?\d{4}",  # com código do país
        r"\(\d{2}\)[\s.\-]?9?[\s.\-]?\d{4}[\s.\-]?\d{4}",  # DDD entre parênteses
        r"\b\d{2}[\s.\-]9[\s.\-]?\d{4}[\s.\-]?\d{4}\b",  # DDD solto + nono dígito
        r"\b\d{2}9\d{8}\b",  # 11 dígitos colados
        r"\b9[\s.\-]?\d{4}[\s.\-]?\d{4}\b",  # celular sem DDD, como se escreve na cidade
        r"\b[2-5]\d{3}[\s.\-]\d{4}\b",  # fixo sem DDD
    )
)

# A ordem é a regra: o padrão mais longo e mais específico corre primeiro, senão o
# curto come um pedaço do longo e o resto vaza. CPF vem depois do telefone porque
# telefone tem forma reconhecível; cartão vem antes de tudo que é numérico curto.
_REDACOES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[EMAIL-REMOVIDO]"),
    (re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b"), "[CNPJ-REMOVIDO]"),
    (re.compile(r"\b(?:\d[ -]?){16}\b"), "[CARTAO-REMOVIDO]"),
    (re.compile(_TELEFONE), "[TELEFONE-REMOVIDO]"),
    (re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"), "[CPF-REMOVIDO]"),
    # Sem IGNORECASE a placa passa em claro: no celular ninguém digita em maiúscula.
    (
        re.compile(r"\b[A-Z]{3}\d[A-Z]\d{2}\b|\b[A-Z]{3}-?\d{4}\b", re.IGNORECASE),
        "[PLACA-REMOVIDA]",
    ),
)


def redigir(texto: str) -> str:
    """S-09 §4 — roda em toda mensagem do cliente antes de montar o prompt.

    O texto original continua em `mensagens.conteudo`: o Raí lê a conversa como ela
    aconteceu. A redação é para o que **sai** da infraestrutura da Sol & Volt.
    """
    for padrao, marcador in _REDACOES:
        texto = padrao.sub(marcador, texto)
    return texto
