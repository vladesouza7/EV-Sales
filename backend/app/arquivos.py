"""ADR-013 — o armazenamento de arquivo do EV-Sales.

Um bucket, privado, com dois prefixos que têm risco diferente:

- `fotos/` — foto de unidade. Não tem PII, e o catálogo é público de qualquer forma. Sai
  pelo proxy do próprio app, o que mantém o MinIO sem porta exposta.
- `documentos/` — o Espelho de Condição e Reserva. **Tem o nome do cliente e o chassi
  reservado para ele.** Só sai por URL assinada, com expiração, emitida por fluxo
  autenticado (S-04).

A separação é por **prefixo**, não por objeto: política por objeto é política que alguém
esquece de aplicar no objeto seguinte.
"""

import io
import logging
import os
import re
from datetime import timedelta

from minio import Minio
from minio.error import S3Error

logger = logging.getLogger(__name__)

BUCKET = os.environ.get("EVSALES_MINIO_BUCKET", "evsales")
PREFIXO_FOTOS = "fotos/"
PREFIXO_DOCUMENTOS = "documentos/"

# A URL assinada do Espelho vale por uma sessão de decisão da Neuza, não por um dia.
VALIDADE_DA_URL = timedelta(minutes=15)

# Um nome de arquivo, e nada além disso: letras, dígitos, ponto, hífen e sublinhado. Sem
# barra, sem barra invertida, sem `..`. É o que impede `/fotos/../documentos/espelho.pdf`.
_NOME_VALIDO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")


class NomeInvalido(ValueError):
    """O nome não é um nome de arquivo. Não vira caminho de objeto."""


def caminho_da_foto(nome: str) -> str:
    """Valida antes de concatenar — a ordem inversa é o bug.

    `..` é recusado explicitamente porque a regex sozinha o aceitaria: são dois pontos, e
    ponto está na lista de caracteres permitidos.
    """
    if not _NOME_VALIDO.match(nome) or ".." in nome:
        raise NomeInvalido(nome)
    return f"{PREFIXO_FOTOS}{nome}"


def _cliente() -> Minio | None:
    endereco = os.environ.get("EVSALES_MINIO_ENDPOINT", "")
    chave = os.environ.get("EVSALES_MINIO_ACCESS_KEY", "")
    segredo = os.environ.get("EVSALES_MINIO_SECRET_KEY", "")
    if not (endereco and chave and segredo):
        return None
    return Minio(
        endereco,
        access_key=chave,
        secret_key=segredo,
        # TLS fica no nginx do perfil prod; dentro da rede do compose o tráfego não sai.
        secure=os.environ.get("EVSALES_MINIO_SEGURO", "").lower() == "true",
    )


def garantir_bucket() -> None:
    """Idempotente, e o bucket nasce privado — que é o padrão do MinIO."""
    cliente = _cliente()
    if cliente is None:
        return
    if not cliente.bucket_exists(BUCKET):
        cliente.make_bucket(BUCKET)


def guardar(caminho: str, conteudo: bytes, tipo: str) -> str:
    """Grava e devolve o caminho do objeto. Quem chama já validou o nome."""
    cliente = _cliente()
    if cliente is None:
        raise RuntimeError("MinIO não configurado")
    cliente.put_object(BUCKET, caminho, io.BytesIO(conteudo), len(conteudo), content_type=tipo)
    return caminho


def ler(caminho: str) -> tuple[bytes, str] | None:
    """Devolve conteúdo e content-type, ou `None` se não existe ou o storage está fora.

    Storage fora do ar não pode derrubar o catálogo: a página cai no `sem-foto.svg`, que é
    degradação visível e inofensiva, em vez de erro 500 numa vitrine.
    """
    cliente = _cliente()
    if cliente is None:
        return None
    resposta = None
    try:
        resposta = cliente.get_object(BUCKET, caminho)
        return resposta.read(), resposta.headers.get("Content-Type", "application/octet-stream")
    except S3Error:
        return None
    except Exception:
        # Sem `str(erro)` no log: o caminho do objeto pode conter o número do Espelho.
        logger.warning("storage indisponível ao ler objeto")
        return None
    finally:
        if resposta is not None:
            resposta.close()
            resposta.release_conn()


def url_assinada_do_documento(nome: str) -> str:
    """S-04 — o Espelho sai por aqui, e por lugar nenhum mais.

    Nunca chame isto a partir de rota pública: a URL é a credencial. Quem decide se este
    cliente pode ver este documento é a tela autenticada, não esta função.
    """
    cliente = _cliente()
    if cliente is None:
        raise RuntimeError("MinIO não configurado")
    if not _NOME_VALIDO.match(nome) or ".." in nome:
        raise NomeInvalido(nome)
    return cliente.presigned_get_object(
        BUCKET, f"{PREFIXO_DOCUMENTOS}{nome}", expires=VALIDADE_DA_URL
    )
