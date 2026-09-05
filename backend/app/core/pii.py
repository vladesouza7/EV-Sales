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
    digitos = re.sub(r"\D", "", e164)[-11:]
    return f"({digitos[:2]}) *****-{digitos[-4:]}"


def mascarar_nome(nome: str) -> str:
    partes = nome.split()
    return partes[0] if len(partes) == 1 else f"{partes[0]} {partes[-1][0]}."


_REDACOES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[EMAIL-REMOVIDO]"),
    (re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b"), "[CNPJ-REMOVIDO]"),
    (re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"), "[CPF-REMOVIDO]"),
    (re.compile(r"\b[A-Z]{3}\d[A-Z]\d{2}\b|\b[A-Z]{3}-?\d{4}\b"), "[PLACA-REMOVIDA]"),
    (re.compile(r"\b(?:\d[ -]?){16}\b"), "[CARTAO-REMOVIDO]"),
    (re.compile(r"(?:\+?55\s*)?\(?\d{2}\)?\s*9?\d{4}[-\s]?\d{4}"), "[TELEFONE-REMOVIDO]"),
)


def redigir(texto: str) -> str:
    """S-09 §4 — roda em toda mensagem do cliente antes de montar o prompt.

    O texto original continua em `mensagens.conteudo`: o Raí lê a conversa como ela
    aconteceu. A redação é para o que **sai** da infraestrutura da Sol & Volt.
    """
    for padrao, marcador in _REDACOES:
        texto = padrao.sub(marcador, texto)
    return texto
