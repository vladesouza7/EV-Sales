"""Tools de leitura de estoque (S-03 §2).

O catálogo público da S-01 §6 chama exatamente estas funções — nunca uma consulta
paralela. É o que garante que catálogo e Aurora nunca discordem.

A frase da autonomia é montada **aqui**, não no prompt e não no HTML. É a invariante 6
virando código: se o rótulo fosse instrução ("sempre diga que WLTP é otimista"), ele
sobreviveria até a primeira reescrita de prompt.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modelos import Unidade

# S-03 §1. Cada fonte tem a sua frase, e a do WLTP nunca sai sem a ressalva: todo
# importado publica WLTP, todo chinês publica Inmetro, e o WLTP é sistematicamente mais
# otimista. Citar 614 km sem dizer isso é dizer um número certo de um jeito errado.
_FRASE_POR_FONTE = {
    "WLTP": "{km} km pelo padrão europeu WLTP — o número do Inmetro costuma ser menor",
    "INMETRO_PBEV_2026": "{km} km pelo Inmetro, no PBEV 2026",
    "FABRICANTE": "{km} km declarados pelo fabricante, sem medição independente",
}
SEM_AUTONOMIA = "autonomia ainda não medida — vou confirmar com o vendedor"


def _autonomia_texto(unidade: Unidade) -> str:
    """ADR-003: valor ausente vira "vou confirmar", nunca estimativa nem regra de três."""
    if unidade.autonomia_km is None or unidade.autonomia_fonte is None:
        return SEM_AUTONOMIA
    return _FRASE_POR_FONTE[unidade.autonomia_fonte].format(km=unidade.autonomia_km)


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
        "autonomia_texto": _autonomia_texto(unidade),
        "foto_url": unidade.foto_url,
    }


def buscar_unidades(
    sessao: Session,
    preco_max_centavos: int | None = None,
    condicao: str | None = None,
    autonomia_min_km: int | None = None,
) -> list[dict[str, object]]:
    """Só o que está `disponivel`. O filtro está na query, não em pós-processamento.

    A Aurora não tem como ver um carro vendido — não porque o prompt proíbe, mas porque
    a linha não volta do banco.
    """
    consulta = select(Unidade).where(Unidade.status == "disponivel")
    if preco_max_centavos is not None:
        consulta = consulta.where(Unidade.preco_centavos <= preco_max_centavos)
    if condicao is not None:
        consulta = consulta.where(Unidade.condicao == condicao)
    if autonomia_min_km is not None:
        # Autonomia nula fica de fora de um filtro por autonomia: entrar seria afirmar
        # que ela atende o mínimo, e não sabemos.
        consulta = consulta.where(Unidade.autonomia_km >= autonomia_min_km)
    consulta = consulta.order_by(Unidade.preco_centavos, Unidade.chassi)
    return [_ficha(u) for u in sessao.scalars(consulta)]


def detalhar_unidade(sessao: Session, chassi: str) -> dict[str, object] | None:
    unidade = sessao.get(Unidade, chassi)
    if unidade is None or unidade.status != "disponivel":
        return None
    return _ficha(unidade)


AVISO_FONTES_DIFERENTES = (
    "As autonomias vêm de padrões de medição diferentes ({fontes}) e não podem ser "
    "comparadas como equivalentes: o WLTP é europeu e sistematicamente mais otimista que "
    "o Inmetro. Apresente os dois números com a fonte de cada um e não diga qual roda mais."
)
AVISO_SEM_AUTONOMIA = (
    "Pelo menos uma das unidades está sem autonomia confirmada. Diga que vai confirmar "
    "com o vendedor e não compare autonomia neste caso."
)


def listar_para_o_modelo(unidades: list[dict[str, object]]) -> dict[str, object]:
    """A mesma proteção da `comparar_unidades`, para quem só pediu a lista.

    A `comparar_unidades` recusa comparar padrões diferentes porque devolve um `aviso`
    estruturado — e o docstring dela diz por quê: "a recusa é retorno de tool, e não uma
    linha de prompt". A `buscar_unidades` não tinha isso, e uma listagem com WLTP e
    Inmetro juntos chegava ao modelo sem nada dizendo que os números não se comparam.
    Foi assim que a Aurora disse "mais autonomia" entre um 533 WLTP e um 481 Inmetro
    (S-03 §8, caso auto-08).

    Só o caminho do modelo passa por aqui. O `/api/catalogo` da S-01 §6 e o MCP seguem
    recebendo a lista crua — o aviso é instrução de conduta, não dado de catálogo.
    """
    fontes = {u.get("autonomia_fonte") for u in unidades if u.get("autonomia_km") is not None}
    sem_medicao = any(u.get("autonomia_km") is None for u in unidades)

    if len(fontes) > 1:
        aviso: str | None = AVISO_FONTES_DIFERENTES.format(
            fontes=" e ".join(sorted(str(f) for f in fontes))
        )
    elif sem_medicao:
        aviso = AVISO_SEM_AUTONOMIA
    else:
        aviso = None

    return {"unidades": unidades, "autonomia_comparavel": aviso is None, "aviso": aviso}


def comparar_unidades(sessao: Session, chassis: list[str]) -> dict[str, object]:
    """S-03 §1 — o lugar onde a invariante 6 é cumprida.

    Este é o único risco do projeto que a verificação numérica da §4 **não** pega: ali os
    números estão certos e o erro está na comparação. Por isso a recusa é retorno de tool,
    e não uma linha de prompt — o modelo recebe os dois valores rotulados e um aviso
    estruturado, e não tem de onde tirar a conclusão de qual roda mais.
    """
    if not 2 <= len(chassis) <= 3:
        raise ValueError("A comparação é de 2 ou 3 unidades.")

    fichas = [f for f in (detalhar_unidade(sessao, c) for c in chassis) if f is not None]
    encontrados = {str(f["chassi"]) for f in fichas}
    fontes = {f["autonomia_fonte"] for f in fichas}

    if None in fontes:
        aviso: str | None = AVISO_SEM_AUTONOMIA
    elif len(fontes) > 1:
        aviso = AVISO_FONTES_DIFERENTES.format(fontes=" e ".join(sorted(str(f) for f in fontes)))
    else:
        aviso = None

    return {
        "unidades": fichas,
        "autonomia_comparavel": aviso is None,
        "aviso": aviso,
        "nao_encontrados": [c for c in chassis if c not in encontrados],
    }
