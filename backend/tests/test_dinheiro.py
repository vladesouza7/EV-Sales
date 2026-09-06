"""S-04 §5 — o valor por extenso do Espelho.

Não é enfeite: é o que impede um algarismo alterado de passar despercebido. Documento com
valor errado por extenso é pior que documento sem valor por extenso, então isto é testado.
"""

import pytest

from app.core.dinheiro import formatar, por_extenso


@pytest.mark.parametrize(
    ("centavos", "esperado"),
    [
        (11890000, "R$ 118.900,00"),
        (24999000, "R$ 249.990,00"),
        (52900000, "R$ 529.000,00"),
        (100, "R$ 1,00"),
        (12345, "R$ 123,45"),
    ],
)
def test_formata_em_algarismos(centavos: int, esperado: str) -> None:
    assert formatar(centavos) == esperado


@pytest.mark.parametrize(
    ("centavos", "esperado"),
    [
        (24999000, "duzentos e quarenta e nove mil, novecentos e noventa reais"),
        (11890000, "cento e dezoito mil e novecentos reais"),
        (10000000, "cem mil reais"),
        (100000000, "um milhão de reais"),
        (250000000, "dois milhões e quinhentos mil reais"),
        (300000000, "três milhões de reais"),
        (100, "um real"),
        (15000, "cento e cinquenta reais"),
        (12345, "cento e vinte e três reais e quarenta e cinco centavos"),
        (1, "um centavo"),
        (0, "zero real"),
    ],
)
def test_escreve_por_extenso(centavos: int, esperado: str) -> None:
    assert por_extenso(centavos) == esperado


def test_recusa_o_que_nao_sabe_escrever() -> None:
    """Errar em silêncio num documento é pior do que recusar."""
    with pytest.raises(ValueError):
        por_extenso(-1)
    with pytest.raises(ValueError):
        por_extenso(10**12)
