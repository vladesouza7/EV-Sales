"""S-03 §1 e §2 — as tools, e a invariante 6 sendo cumprida por código.

A verificação numérica da §4 aprovaria "o Ioniq roda o dobro do Dolphin": os dois números
existem no banco. Quem recusa a comparação é a tool, e é por isso que estes testes são
tão importantes quanto os do `test_verificacao.py`.
"""

import uuid

import pytest
from sqlalchemy.orm import Session

from app.core.pii import cifrar, hash_telefone
from app.db import agora
from app.ia.tools.estoque import (
    buscar_unidades,
    comparar_unidades,
    detalhar_unidade,
    listar_para_o_modelo,
)
from app.ia.tools.qualificacao import registrar_qualificacao, transferir_para_humano
from app.modelos import Conversa, Lead, Unidade

SEAL = dict(
    chassi="9BWZZZ377VT004471", marca="BYD", modelo="Seal", versao="Design", ano=2026,
    cor="Branco", condicao="novo", km=0, preco_centavos=24999000,
    autonomia_km=372, autonomia_fonte="INMETRO_PBEV_2026", status="disponivel",
)  # fmt: skip
IONIQ = dict(
    SEAL, chassi="KMHZZZ111NA09902", marca="Hyundai", modelo="Ioniq 6", versao="RWD",
    condicao="seminovo", km=18500, preco_centavos=31900000,
    autonomia_km=614, autonomia_fonte="WLTP",
)  # fmt: skip
TAYCAN = dict(
    SEAL, chassi="WP0ZZZY1ZKSA01234", marca="Porsche", modelo="Taycan", versao="4S",
    condicao="seminovo", km=31000, preco_centavos=52900000,
    autonomia_km=None, autonomia_fonte=None,
)  # fmt: skip


@pytest.fixture
def estoque(sessao: Session) -> Session:
    sessao.add_all(Unidade(**u) for u in (SEAL, IONIQ, TAYCAN))  # type: ignore[arg-type]
    sessao.commit()
    return sessao


@pytest.fixture
def conversa(sessao: Session) -> Conversa:
    lead = Lead(
        nome_cifrado=cifrar("Tarcísio Nóbrega"),
        telefone_cifrado=cifrar("+5583988714471"),
        telefone_hash=hash_telefone("+5583988714471"),
    )
    sessao.add(lead)
    sessao.flush()
    conversa = Conversa(
        lead_id=lead.id,
        etapa="qualificacao",
        token_sessao=uuid.uuid4().hex,
        token_expira_em=agora(),
    )
    sessao.add(conversa)
    sessao.commit()
    return conversa


def test_wltp_nunca_sai_sem_o_rotulo(estoque: Session) -> None:
    ioniq = detalhar_unidade(estoque, IONIQ["chassi"])  # type: ignore[arg-type]
    assert ioniq is not None
    assert "614" in ioniq["autonomia_texto"]  # type: ignore[operator]
    assert "WLTP" in ioniq["autonomia_texto"]  # type: ignore[operator]
    assert "Inmetro" in ioniq["autonomia_texto"]  # type: ignore[operator]


def test_autonomia_nula_vira_vou_confirmar_e_nao_estimativa(estoque: Session) -> None:
    taycan = detalhar_unidade(estoque, TAYCAN["chassi"])  # type: ignore[arg-type]
    assert taycan is not None
    assert taycan["autonomia_km"] is None
    texto = str(taycan["autonomia_texto"])
    assert "confirmar" in texto
    assert not any(c.isdigit() for c in texto)


def test_comparar_recusa_wltp_contra_inmetro(estoque: Session) -> None:
    comparacao = comparar_unidades(estoque, [SEAL["chassi"], IONIQ["chassi"]])  # type: ignore[list-item]
    assert comparacao["autonomia_comparavel"] is False
    aviso = str(comparacao["aviso"])
    assert "WLTP" in aviso and "Inmetro" in aviso
    # Os dois números continuam sendo entregues — recusar comparar não é esconder.
    textos = [str(u["autonomia_texto"]) for u in comparacao["unidades"]]  # type: ignore[index,union-attr]
    assert any("372" in t for t in textos) and any("614" in t for t in textos)


def test_comparar_mesma_fonte_e_comparavel(estoque: Session) -> None:
    outro = dict(SEAL, chassi="9BWZZZ377VT005555", modelo="Dolphin", autonomia_km=291)
    estoque.add(Unidade(**outro))  # type: ignore[arg-type]
    estoque.commit()

    comparacao = comparar_unidades(estoque, [SEAL["chassi"], outro["chassi"]])  # type: ignore[list-item]
    assert comparacao["autonomia_comparavel"] is True
    assert comparacao["aviso"] is None


def test_comparar_com_autonomia_nula_nao_e_comparavel(estoque: Session) -> None:
    comparacao = comparar_unidades(estoque, [SEAL["chassi"], TAYCAN["chassi"]])  # type: ignore[list-item]
    assert comparacao["autonomia_comparavel"] is False


def test_comparar_aceita_no_maximo_tres(estoque: Session) -> None:
    with pytest.raises(ValueError):
        comparar_unidades(estoque, [SEAL["chassi"]])  # type: ignore[list-item]


def test_detalhar_nao_entrega_unidade_vendida(estoque: Session) -> None:
    unidade = estoque.get(Unidade, SEAL["chassi"])
    assert unidade is not None
    unidade.status = "vendido"
    estoque.commit()
    assert detalhar_unidade(estoque, SEAL["chassi"]) is None  # type: ignore[arg-type]


def test_buscar_filtra_por_preco_maximo(estoque: Session) -> None:
    achados = buscar_unidades(estoque, preco_max_centavos=32000000)
    assert {str(u["chassi"]) for u in achados} == {SEAL["chassi"], IONIQ["chassi"]}


def test_buscar_filtra_por_condicao(estoque: Session) -> None:
    achados = buscar_unidades(estoque, condicao="novo")
    assert {str(u["chassi"]) for u in achados} == {SEAL["chassi"]}


def test_buscar_sem_filtro_devolve_o_patio_inteiro(estoque: Session) -> None:
    """A tool sempre soube do seminovo — o filtro é que era escolha da Aurora.

    No eval (S-03 §8: preco-06, preco-10, auto-02) ela chamava `condicao="novo"` por
    conta própria, recebia só os novos e dizia que o Taycan não existia. Metade do
    pátio é seminovo premium: um filtro padrão aqui esconderia estoque de verdade.
    """
    achados = buscar_unidades(estoque)
    esperados = {str(SEAL["chassi"]), str(IONIQ["chassi"]), str(TAYCAN["chassi"])}
    assert {str(u["chassi"]) for u in achados} == esperados


def test_listagem_com_fontes_misturadas_avisa_que_nao_da_pra_comparar(
    estoque: Session,
) -> None:
    """A `comparar_unidades` já recusava; a listagem não avisava nada.

    O pátio tem Inmetro e WLTP juntos, e sem o aviso a Aurora disse "mais autonomia"
    entre um 533 WLTP e um 481 Inmetro (S-03 §8, auto-08). A invariante 6 é a única que
    a verificação numérica não pega: ali os números estão certos e o erro é a comparação.
    """
    resultado = listar_para_o_modelo(buscar_unidades(estoque))

    assert resultado["autonomia_comparavel"] is False
    aviso = str(resultado["aviso"])
    assert "não podem ser comparadas" in aviso and "não diga qual roda mais" in aviso
    assert len(list(resultado["unidades"])) == 3  # type: ignore[arg-type]


def test_listagem_de_uma_fonte_so_nao_inventa_aviso(estoque: Session) -> None:
    """Aviso que aparece sempre é aviso que ninguém lê."""
    resultado = listar_para_o_modelo(buscar_unidades(estoque, condicao="novo"))

    assert resultado["autonomia_comparavel"] is True and resultado["aviso"] is None


def test_o_catalogo_publico_nao_recebe_o_aviso(estoque: Session) -> None:
    """S-01 §6 — a tela e o MCP continuam com a lista crua. Aviso é conduta, não dado."""
    assert isinstance(buscar_unidades(estoque), list)


def test_qualificacao_acumula_sem_apagar_o_que_ja_foi_respondido(
    sessao: Session, conversa: Conversa
) -> None:
    registrar_qualificacao(sessao, conversa, uso="cidade", km_dia=40)
    registrar_qualificacao(sessao, conversa, tem_carregador=True)

    assert conversa.qualificacao == {"uso": "cidade", "km_dia": 40, "tem_carregador": True}


def test_transferir_para_humano_tira_a_aurora_da_conversa(
    sessao: Session, conversa: Conversa
) -> None:
    transferir_para_humano(sessao, conversa, motivo="cliente pediu desconto")

    assert conversa.modo == "humano"
    assert conversa.etapa == "humano"
