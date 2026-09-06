import base64
import os
from collections.abc import Iterator

os.environ.setdefault("EVSALES_PII_KEY", base64.b64encode(b"k" * 32).decode())
os.environ.setdefault("EVSALES_PII_PEPPER", base64.b64encode(b"p" * 32).decode())
os.environ.setdefault("EVSALES_JWT_SECRET", base64.b64encode(b"j" * 32).decode())
os.environ.setdefault(
    "EVSALES_DATABASE_URL",
    "postgresql+psycopg://evsales:evsales@localhost:5432/evsales_test",
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.ia import turno as modulo_turno  # noqa: E402
from app.limite import limpar_limites  # noqa: E402
from app.main import app  # noqa: E402

from .dubles import ProvedorDuble  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema() -> Iterator[None]:
    # Os EXCLUDE da S-07 são gist sobre uuid e text: sem btree_gist o create_all falha.
    with engine.begin() as conexao:
        conexao.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _banco_limpo() -> Iterator[None]:
    limpar_limites()
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


@pytest.fixture(autouse=True)
def provedor(monkeypatch: pytest.MonkeyPatch) -> ProvedorDuble:
    """Nenhum teste fala com o OpenRouter. Autouse porque um teste que esquecesse de
    trocar o provedor tentaria a rede de verdade — e falharia por motivo errado."""
    duble = ProvedorDuble()
    monkeypatch.setattr(modulo_turno, "PROVEDOR", duble)
    return duble
