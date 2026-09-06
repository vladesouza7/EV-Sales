"""S-03 §4 — a verificação numérica de saída, o mecanismo central do ADR-003.

Nenhum destes testes precisa de modelo: a verificação é determinística de propósito. É
ela que decide se o texto sai, e um mecanismo que só dá para testar chamando o provedor
seria um mecanismo que ninguém roda no CI.
"""

from app.ia.verificacao import permitidos_de, verificar

SEAL = {
    "chassi": "9BWZZZ377VT004471",
    "marca": "BYD",
    "modelo": "Seal",
    "preco_centavos": 24999000,  # R$ 249.990
    "autonomia_km": 372,
    "autonomia_fonte": "INMETRO_PBEV_2026",
    "km": 0,
    "ano": 2026,
}

PERMITIDOS = permitidos_de([SEAL])


def test_preco_que_veio_da_tool_passa() -> None:
    assert verificar("Esse Seal sai por R$ 249.990.", PERMITIDOS).aprovado


def test_preco_que_nao_veio_de_tool_nenhuma_e_reprovado() -> None:
    veredito = verificar("Esse Seal sai por R$ 239.990.", PERMITIDOS)
    assert not veredito.aprovado
    assert "R$ 239.990" in " ".join(veredito.divergentes)


def test_arredondar_para_baixo_com_marcador_passa() -> None:
    assert verificar("Faz mais de 370 km com uma carga.", PERMITIDOS).aprovado


def test_arredondar_para_cima_e_reprovado() -> None:
    assert not verificar("Faz cerca de 400 km com uma carga.", PERMITIDOS).aprovado
    assert not verificar("Faz quase 400 km com uma carga.", PERMITIDOS).aprovado


def test_arredondar_para_baixo_longe_demais_e_reprovado() -> None:
    """ "Mais de 100 km" para 372 é verdade e é mentira ao mesmo tempo."""
    assert not verificar("Faz mais de 100 km com uma carga.", PERMITIDOS).aprovado


def test_numero_por_extenso_nao_escapa_da_regex() -> None:
    """O buraco óbvio: o modelo escreve o preço em palavras e a regex de dígito não vê nada."""
    assert not verificar("Fica em cento e cinquenta mil.", PERMITIDOS).aprovado
    assert verificar("Fica em duzentos e quarenta e nove mil.", PERMITIDOS).aprovado is False


def test_cento_e_cinquenta_mil_do_proprio_cliente_passa() -> None:
    """Condição 3 da §4 — ele disse que tem 150 mil; repetir isso não é inventar preço."""
    veredito = verificar(
        "Com cento e cinquenta mil dá pra ver umas opções boas.",
        PERMITIDOS,
        do_cliente="tenho 150 mil pra gastar",
    )
    assert veredito.aprovado


def test_valor_do_cliente_em_numeral_passa() -> None:
    assert verificar(
        "Com R$ 150.000 eu te mostro três carros.",
        PERMITIDOS,
        do_cliente="tenho uns 150 mil",
    ).aprovado


def test_hora_e_ano_nao_sao_numero_comercial() -> None:
    assert verificar("Pode ser quinta às 14h, no ano 2026?", PERMITIDOS).aprovado


def test_potencia_inventada_e_reprovada() -> None:
    """Não há coluna de potência: qualquer `cv` na resposta veio do modelo."""
    assert not verificar("Tem 230 cv de potência.", PERMITIDOS).aprovado


def test_prazo_de_entrega_inventado_e_reprovado() -> None:
    assert not verificar("A entrega leva 30 dias.", PERMITIDOS).aprovado


def test_km_do_odometro_passa() -> None:
    usado = {**SEAL, "km": 18500, "chassi": "9BWZZZ377VT009902"}
    assert verificar("Está com 18.500 km rodados.", permitidos_de([usado])).aprovado


def test_texto_sem_numero_passa() -> None:
    veredito = verificar("Me conta como você usa o carro no dia a dia.", PERMITIDOS)
    assert veredito.aprovado and veredito.extraidos == []
