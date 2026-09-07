"""A única saída HTTP do EV-Sales para serviço de terceiro configurado por gente.

Dois chamadores hoje: a sonda da [S-12 §5] e o envio pela Evolution da [S-06 §6]. Os dois
falam com uma URL que alguém digitou numa tela, e é isso que justifica o arquivo existir —
a regra de não seguir redirecionamento vale nos dois, e uma cópia dela em cada módulo é a
cópia que um dia diverge.

O provedor de LLM **não** passa por aqui: a URL dele vem de preset, o corpo carrega a
conversa do cliente, e ele tem o próprio tratamento de erro em `app/ia/provedor.py`.
"""

import urllib.error
import urllib.request

TEMPO_LIMITE_S = 5


class Indisponivel(RuntimeError):
    """O destino não respondeu. Não é o mesmo que ter recusado a credencial."""


class SemRedirecionamento(urllib.request.HTTPRedirectHandler):
    """Seguir redirecionamento é como uma URL externa vira interna.

    Quem controla o destino responde `302 Location: http://169.254.169.254/…` e a
    requisição sai da rede da loja para dentro dela. Recusar devolvendo `None` faz o
    `urllib` levantar `HTTPError` com o 302, e quem chama trata como recusa.

    Substituir e não omitir: `build_opener` reinstala os handlers padrão que faltarem, e um
    handler ausente da lista voltaria sozinho.
    """

    def redirect_request(self, *_: object, **__: object) -> None:
        return None


def abridor() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(SemRedirecionamento)


def buscar(url: str, cabecalhos: dict[str, str], corpo: bytes | None = None) -> int:
    """Devolve **só o status**. O corpo do destino nunca sai desta função.

    Devolvê-lo faria de quem chama um leitor de qualquer endereço que o servidor alcança.
    """
    requisicao = urllib.request.Request(url, data=corpo, headers=cabecalhos)
    try:
        with abridor().open(requisicao, timeout=TEMPO_LIMITE_S) as resposta:
            return int(resposta.status)
    except urllib.error.HTTPError as erro:
        return int(erro.code)
    except (urllib.error.URLError, TimeoutError, OSError) as erro:
        raise Indisponivel(type(erro).__name__) from None
