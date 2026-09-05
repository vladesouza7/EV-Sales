"""ADR-001 — o Postgres é a fonte da verdade. Não há segunda cópia autoritativa."""

import os
from collections.abc import Iterator
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

FUSO = ZoneInfo("America/Fortaleza")  # a Paraíba não tem horário de verão

URL = os.environ.get(
    "EVSALES_DATABASE_URL", "postgresql+psycopg://evsales:evsales@localhost:5432/evsales"
)

engine = create_engine(URL, pool_pre_ping=True)
Sessao = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def agora() -> datetime:
    return datetime.now(FUSO)


def obter_sessao() -> Iterator[Session]:
    with Sessao() as sessao:
        yield sessao
