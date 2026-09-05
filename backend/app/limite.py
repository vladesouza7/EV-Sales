"""S-01 §5 — limite por IP.

ponytail: contador em memória do processo. Vira contador no Redis quando a api rodar
com mais de um worker — antes disso, o Redis só adicionaria uma caixa sem ganho.
"""

import time
from collections import defaultdict

_eventos: dict[str, list[float]] = defaultdict(list)


def dentro_do_limite(chave: str, *, maximo: int, janela_segundos: int) -> bool:
    agora = time.monotonic()
    recentes = [t for t in _eventos[chave] if agora - t < janela_segundos]
    if len(recentes) >= maximo:
        _eventos[chave] = recentes
        return False
    recentes.append(agora)
    _eventos[chave] = recentes
    return True


def limpar_limites() -> None:
    _eventos.clear()
