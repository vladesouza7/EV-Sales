"""Tools de leitura de estoque (S-03 §2).

O catálogo público da S-01 §6 chama exatamente esta função — nunca uma consulta
paralela. É o que garante que catálogo e Aurora nunca discordem.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modelos import Unidade


def _ficha(unidade: Unidade) -> dict[str, object]:
    return {
        "chassi": unidade.chassi,
        "marca": unidade.marca,
        "modelo": unidade.modelo,
        "versao": unidade.versao,
        "ano": unidade.ano,
        "cor": unidade.cor,
        "condicao": unidade.condicao,
        "km": unidade.km,
        "preco_centavos": unidade.preco_centavos,
        # ADR-003: a autonomia nunca viaja sem a fonte. WLTP e Inmetro não se comparam.
        "autonomia_km": unidade.autonomia_km,
        "autonomia_fonte": unidade.autonomia_fonte,
        "foto_url": unidade.foto_url,
    }


def buscar_unidades(sessao: Session) -> list[dict[str, object]]:
    """Só o que está `disponivel`. O filtro está na query, não em pós-processamento."""
    consulta = (
        select(Unidade)
        .where(Unidade.status == "disponivel")
        .order_by(Unidade.preco_centavos, Unidade.chassi)
    )
    return [_ficha(u) for u in sessao.scalars(consulta)]
