"""S-12 §6 e ADR-014 — o leitor único da configuração.

```
configuracoes (banco)  →  .env  →  o padrão do preset
```

Duas fontes para o mesmo valor ficam corretas enquanto existe um leitor, e divergem no dia
em que aparece o segundo. Então há um: este módulo. Quem precisa de configuração chama
`valor()` ou `ambiente()`, e ninguém mais lê `os.environ` para estas chaves.

Nada aqui escreve no `.env`. Ele é gerado pelo `gerar-segredos.sh` e sobrescrito no deploy
seguinte — a troca feita pela tela sumiria sem aviso.
"""

import logging
import os
import time
import uuid
from enum import StrEnum

from cryptography.exceptions import InvalidTag
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.pii import cifrar, decifrar
from app.db import agora
from app.ia.provedor import (
    VARIAVEL_CHAVE,
    VARIAVEL_FALLBACKS,
    VARIAVEL_MODELO,
    VARIAVEL_PROVEDOR,
    VARIAVEL_URL,
)
from app.modelos import Configuracao, Usuario


class Chave(StrEnum):
    """A lista fechada. Chave nova exige commit (ADR-014 §1)."""

    whatsapp_numero = "whatsapp_numero"
    evolution_url = "evolution_url"
    evolution_instancia = "evolution_instancia"
    evolution_chave = "evolution_chave"
    llm_provedor = "llm_provedor"
    llm_modelo = "llm_modelo"
    llm_url = "llm_url"
    llm_chave = "llm_chave"
    llm_fallbacks = "llm_fallbacks"


# O que a tela mostra como `••••` e o que ela mostra por extenso.
SIGILOSAS = frozenset({Chave.evolution_chave, Chave.llm_chave})

# Qual variável de ambiente responde por cada chave quando o banco não tem nada. As cinco
# do provedor são as que o ADR-012 já tinha nomeado — reaproveitadas, não redefinidas.
VARIAVEL: dict[Chave, str] = {
    Chave.whatsapp_numero: "EVSALES_WHATSAPP_NUMERO",
    Chave.evolution_url: "EVSALES_EVOLUTION_URL",
    Chave.evolution_instancia: "EVSALES_EVOLUTION_INSTANCIA",
    Chave.evolution_chave: "EVSALES_EVOLUTION_CHAVE",
    Chave.llm_provedor: VARIAVEL_PROVEDOR,
    Chave.llm_modelo: VARIAVEL_MODELO,
    Chave.llm_url: VARIAVEL_URL,
    Chave.llm_chave: VARIAVEL_CHAVE,
    Chave.llm_fallbacks: VARIAVEL_FALLBACKS,
}

logger = logging.getLogger(__name__)

VALIDADE_CACHE_S = 30

# ponytail: cache em processo. Com o worker da S-10 §1 a invalidação não cruza processos, e
# a janela de divergência é a validade acima — que é o "no máximo 30 segundos" da spec.
_cache: tuple[float, dict[Chave, str]] | None = None


def esquecer() -> None:
    """Invalida o cache. Chamado por toda escrita, e pela suíte entre testes."""
    global _cache
    _cache = None


def _do_banco(sessao: Session) -> dict[Chave, str]:
    global _cache
    if _cache is not None and time.monotonic() - _cache[0] < VALIDADE_CACHE_S:
        return _cache[1]

    guardadas: dict[Chave, str] = {}
    for linha in sessao.scalars(select(Configuracao)).all():
        if linha.chave not in Chave.__members__:
            continue
        try:
            guardadas[Chave(linha.chave)] = decifrar(linha.valor_cifrado)
        except InvalidTag:
            # Chave girada ou blob corrompido. A linha vira "não configurada" e o `valor()`
            # cai no `.env`, como manda o ADR-014 — a mesma postura do `Lead.nome_mascarado`
            # (S-09 §2): registro que não decifra não pode derrubar a tela. Aqui é pior que
            # na fila, porque a tela derrubada é justamente a que reconfiguraria a chave.
            logger.warning("configuracao %s não decifra com a EVSALES_PII_KEY atual", linha.chave)
    _cache = (time.monotonic(), guardadas)
    return guardadas


def valor(sessao: Session, chave: Chave) -> str:
    """O valor em uso: banco, senão `.env`, senão vazio."""
    guardado = _do_banco(sessao).get(chave)
    if guardado is not None:
        return guardado
    return os.environ.get(VARIAVEL[chave], "")


def fonte(sessao: Session, chave: Chave) -> str:
    """De onde o valor em uso está vindo. É o que a tela mostra ao lado do campo.

    Sem isto, o incidente é previsível: alguém edita o `.env`, nada muda, e ninguém entende
    por quê.
    """
    if chave in _do_banco(sessao):
        return "banco"
    return ".env" if os.environ.get(VARIAVEL[chave]) else "padrao"


def divergente(sessao: Session, chave: Chave) -> bool:
    """Vale do banco e existe outra no `.env` — credencial velha, viva, num arquivo."""
    guardado = _do_banco(sessao).get(chave)
    do_arquivo = os.environ.get(VARIAVEL[chave], "")
    return guardado is not None and bool(do_arquivo) and do_arquivo != guardado


def ambiente(sessao: Session) -> dict[str, str]:
    """As variáveis do processo com o banco por cima.

    É o que o `ProvedorCompativel` recebe no lugar do `os.environ`: o ADR-012 já lia tudo
    de um mapa, então a precedência entra sem reescrever o provedor.
    """
    efetivo = dict(os.environ)
    for chave, guardado in _do_banco(sessao).items():
        efetivo[VARIAVEL[chave]] = guardado
    return efetivo


def gravar(sessao: Session, chave: Chave, texto: str, usuario: Usuario) -> None:
    """Cifra, grava, deixa rastro e invalida o cache — nessa ordem, numa transação."""
    linha = sessao.get(Configuracao, chave.value)
    if linha is None:
        linha = Configuracao(chave=chave.value)
        sessao.add(linha)
    linha.valor_cifrado = cifrar(texto)
    linha.atualizado_em = agora()
    linha.atualizado_por = usuario.id
    sessao.commit()
    esquecer()
    _rastro(sessao, chave, usuario.id)


def limpar(sessao: Session, chave: Chave, usuario: Usuario) -> None:
    linha = sessao.get(Configuracao, chave.value)
    if linha is not None:
        sessao.delete(linha)
        sessao.commit()
        esquecer()
    _rastro(sessao, chave, usuario.id)


def _rastro(sessao: Session, chave: Chave, usuario_id: uuid.UUID) -> None:
    """S-12 §7 — quem, quando, qual chave. **Nunca o valor**, nem mascarado.

    Import aqui dentro: `observabilidade` importa `modelos`, e `modelos` não importa este
    módulo — mas o caminho inverso passaria a existir se o import subisse para o topo.
    """
    from app.observabilidade import registrar

    registrar(
        sessao,
        None,
        "evento",
        "configuracao_alterada",
        dados={"chave": chave.value, "usuario_id": str(usuario_id)},
    )
