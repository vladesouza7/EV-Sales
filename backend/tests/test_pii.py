"""S-09 — cifragem, hash indexável e mascaramento na origem."""

import pytest

from app.core.pii import (
    cifrar,
    decifrar,
    hash_telefone,
    mascarar_nome,
    mascarar_telefone,
    redigir,
)
from app.modelos import Lead

TELEFONE = "+5583988714471"


class TestCifragem:
    def test_ida_e_volta(self) -> None:
        assert decifrar(cifrar(TELEFONE)) == TELEFONE

    def test_banco_guarda_ciphertext(self) -> None:
        blob = cifrar(TELEFONE)
        assert b"988714471" not in blob
        assert TELEFONE.encode() not in blob

    def test_nonce_por_registro(self) -> None:
        assert cifrar(TELEFONE) != cifrar(TELEFONE)


class TestHash:
    def test_deterministico(self) -> None:
        assert hash_telefone(TELEFONE) == hash_telefone(TELEFONE)

    def test_distingue_numeros(self) -> None:
        assert hash_telefone(TELEFONE) != hash_telefone("+5583988714472")

    def test_nao_contem_o_numero(self) -> None:
        assert "988714471" not in hash_telefone(TELEFONE)


class TestMascaramento:
    def test_telefone(self) -> None:
        assert mascarar_telefone(TELEFONE) == "(83) *****-4471"

    def test_sobrenome_vira_inicial(self) -> None:
        assert mascarar_nome("Tarcísio Nóbrega") == "Tarcísio N."

    def test_nome_unico_nao_e_mascarado(self) -> None:
        assert mascarar_nome("Tarcísio") == "Tarcísio"

    @pytest.mark.parametrize(
        ("bruto", "esperado"),
        [
            ("meu cpf é 000.000.000-00", "meu cpf é [CPF-REMOVIDO]"),
            ("manda pro tarcisio@email.com", "manda pro [EMAIL-REMOVIDO]"),
            ("a placa é ABC1D23", "a placa é [PLACA-REMOVIDA]"),
            ("meu zap é (83) 98871-4471", "meu zap é [TELEFONE-REMOVIDO]"),
        ],
    )
    def test_redacao_antes_do_prompt(self, bruto: str, esperado: str) -> None:
        assert redigir(bruto) == esperado


class TestDescuidoMaisComum:
    def test_repr_do_lead_ja_sai_protegido(self) -> None:
        lead = Lead(nome_cifrado=cifrar("Tarcísio Nóbrega"), telefone_cifrado=cifrar(TELEFONE))
        texto = repr(lead)
        assert "(83) *****-4471" in texto
        assert "Tarcísio N." in texto
        assert "988714471" not in texto
        assert "Nóbrega" not in texto
