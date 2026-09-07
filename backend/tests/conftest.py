import base64
import os
from collections.abc import Iterator

os.environ.setdefault("EVSALES_PII_KEY", base64.b64encode(b"k" * 32).decode())
os.environ.setdefault("EVSALES_PII_PEPPER", base64.b64encode(b"p" * 32).decode())
os.environ.setdefault("EVSALES_JWT_SECRET", base64.b64encode(b"j" * 32).decode())
# O Espelho da S-04 é um PDF de verdade num bucket de verdade — o mesmo compromisso que a
# suíte já faz com o Postgres. Bucket separado: teste não escreve no balde de trabalho.
os.environ.setdefault("EVSALES_MINIO_ENDPOINT", "localhost:9000")
os.environ.setdefault("EVSALES_MINIO_ACCESS_KEY", "evsales")
os.environ.setdefault("EVSALES_MINIO_SECRET_KEY", "evsales-local")
os.environ.setdefault("EVSALES_MINIO_BUCKET", "evsales-teste")
os.environ.setdefault(
    "EVSALES_DATABASE_URL",
    "postgresql+psycopg://evsales:evsales@localhost:5432/evsales_test",
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.configuracao import (  # noqa: E402
    Chave,
    esquecer,  # noqa: E402
    gravar,
)
from app.db import Base, engine  # noqa: E402
from app.ia import turno as modulo_turno  # noqa: E402
from app.limite import limpar_limites  # noqa: E402
from app.main import app  # noqa: E402

from .dubles import ProvedorDuble  # noqa: E402

# O número da loja e o do lead da suíte são o mesmo valor: dois números diferentes fariam
# o link `wa.me` apontar para um telefone e o envio sair por outro, e nenhum teste veria.
TELEFONE_DA_LOJA = "+5583991575299"


@pytest.fixture(scope="session", autouse=True)
def _schema() -> Iterator[None]:
    # Os EXCLUDE da S-07 são gist sobre uuid e text: sem btree_gist o create_all falha.
    with engine.begin() as conexao:
        conexao.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
        conexao.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _banco_limpo() -> Iterator[None]:
    limpar_limites()
    esquecer()
    with Session(engine) as sessao:
        for tabela in reversed(Base.metadata.sorted_tables):
            sessao.execute(tabela.delete())
        sessao.commit()
    yield


@pytest.fixture
def sessao() -> Iterator[Session]:
    with Session(engine) as s:
        yield s


@pytest.fixture
def cliente() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def configurado(sessao: Session) -> None:
    """A loja com WhatsApp cadastrado. Sem isto não há link nem envio — de propósito.

    Mora aqui porque três arquivos precisam dela: o handoff da S-06, o aviso da S-04 e o
    lembrete da S-07 §6. Importar fixture de um módulo de teste para outro funciona e
    engana o linter; o `conftest` é o lugar em que o pytest já procura.
    """
    from app.autenticacao import criar_usuario

    rai = criar_usuario(sessao, nome="Raí Sol", email="rai@solevolt.com.br",
                        senha="senha-de-teste-12", perfil="dono")  # fmt: skip
    gravar(sessao, Chave.whatsapp_numero, TELEFONE_DA_LOJA, rai)
    gravar(sessao, Chave.evolution_url, "http://evolution:8080", rai)
    gravar(sessao, Chave.evolution_instancia, "solevolt", rai)
    gravar(sessao, Chave.evolution_chave, "chave-da-instancia", rai)


@pytest.fixture
def enviados(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Nada sai para a rede. O que a suíte confere é **se** saiu e com o quê."""
    from app import whatsapp

    saidas: list[dict[str, object]] = []

    def falso(url: str, cabecalhos: dict[str, str], corpo: bytes | None = None) -> int:
        saidas.append({"url": url, "corpo": (corpo or b"").decode()})
        return 200

    monkeypatch.setattr(whatsapp, "buscar", falso)
    return saidas


@pytest.fixture(autouse=True)
def provedor(monkeypatch: pytest.MonkeyPatch) -> ProvedorDuble:
    """Nenhum teste fala com o OpenRouter. Autouse porque um teste que esquecesse de
    trocar o provedor tentaria a rede de verdade — e falharia por motivo errado."""
    duble = ProvedorDuble()
    monkeypatch.setattr(modulo_turno, "PROVEDOR", duble)
    return duble
