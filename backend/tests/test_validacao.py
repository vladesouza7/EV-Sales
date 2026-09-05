"""S-01 §2 e §3 — nome e telefone. Regras da spec, valores da spec."""

import pytest

from app.core.validacao import MSG_SO_CELULAR, normalizar_nome, normalizar_telefone


class TestNome:
    def test_colapsa_espaco_e_nao_capitaliza(self) -> None:
        assert normalizar_nome("  tarcísio   nóbrega ") == "tarcísio nóbrega"

    def test_nome_unico_basta(self) -> None:
        assert normalizar_nome("Tarcísio") == "Tarcísio"

    def test_aceita_acento_apostrofo_e_hifen(self) -> None:
        assert normalizar_nome("D'Ávila Nóbrega-Silva") == "D'Ávila Nóbrega-Silva"

    @pytest.mark.parametrize(
        "bruto",
        ["T", "", "   ", "Tarcisio2", "tarcisio@email.com", "https://x.com", "N" * 81],
    )
    def test_rejeita(self, bruto: str) -> None:
        with pytest.raises(ValueError):
            normalizar_nome(bruto)


class TestTelefone:
    @pytest.mark.parametrize(
        "bruto",
        ["(83) 98871-4471", "83988714471", "+55 83 98871-4471", "5583988714471"],
    )
    def test_normaliza_para_e164(self, bruto: str) -> None:
        assert normalizar_telefone(bruto) == "+5583988714471"

    def test_fixo_recusado_com_orientacao(self) -> None:
        with pytest.raises(ValueError) as erro:
            normalizar_telefone("(83) 3244-1010")
        assert str(erro.value) == MSG_SO_CELULAR

    def test_nono_digito_diferente_de_nove_recusado(self) -> None:
        with pytest.raises(ValueError) as erro:
            normalizar_telefone("(83) 88871-4471")
        assert str(erro.value) == MSG_SO_CELULAR

    @pytest.mark.parametrize("ddd", ["00", "01", "20", "23", "25", "26", "29", "30", "39", "52"])
    def test_ddd_inexistente_recusado(self, ddd: str) -> None:
        with pytest.raises(ValueError):
            normalizar_telefone(f"({ddd}) 98871-4471")

    @pytest.mark.parametrize("bruto", ["", "83 9887", "839887144719999"])
    def test_tamanho_invalido_recusado(self, bruto: str) -> None:
        with pytest.raises(ValueError):
            normalizar_telefone(bruto)
