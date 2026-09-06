"""S-09 — cifragem, hash indexável e mascaramento na origem."""

import re

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
            ("cnpj 12.345.678/0001-95", "cnpj [CNPJ-REMOVIDO]"),
            ("manda pro tarcisio@email.com", "manda pro [EMAIL-REMOVIDO]"),
            ("manda pro tarcisio@email.com.", "manda pro [EMAIL-REMOVIDO]."),
            ("a placa é ABC1D23", "a placa é [PLACA-REMOVIDA]"),
            ("a placa e abc1d23", "a placa e [PLACA-REMOVIDA]"),
            ("cartao 4111 1111 1111 1111", "cartao [CARTAO-REMOVIDO]"),
        ],
    )
    def test_redacao_antes_do_prompt(self, bruto: str, esperado: str) -> None:
        assert redigir(bruto) == esperado

    @pytest.mark.parametrize(
        "bruto",
        [
            "meu zap é (83) 98871-4471",
            "meu zap é +55 83 98871-4471",
            "meu zap é 5583988714471",
            "meu zap é 83988714471",
            "meu zap é 83 98871-4471",
            # Como se escreve o próprio número para uma loja da cidade: sem DDD.
            "meu zap é 98871-4471",
            "meu zap é 988714471",
            "meu zap é 83 9 8871 4471",
            # Fixo da loja também é telefone.
            "liga no 3244-1010",
        ],
    )
    def test_nenhum_formato_de_telefone_escapa(self, bruto: str) -> None:
        """S-09 §4.2 é categórico: o telefone nunca entra no prompt."""
        redigido = redigir(bruto)
        assert "[TELEFONE-REMOVIDO]" in redigido or "[CPF-REMOVIDO]" in redigido
        assert not re.search(r"\d{4}", redigido), redigido

    def test_numero_do_dominio_nao_e_confundido_com_telefone(self) -> None:
        """Preço e autonomia atravessam a redação intactos — senão o prompt perde o carro."""
        assert redigir("o Seal custa R$ 249.990 e faz 372 km") == (
            "o Seal custa R$ 249.990 e faz 372 km"
        )
        assert redigir("são 24999000 centavos") == "são 24999000 centavos"


class TestMascaraNaoInventa:
    def test_entrada_curta_nao_vira_ddd_falso(self) -> None:
        """`3244-1010` não tem DDD; `(32) *****-1010` seria a máscara mentindo."""
        assert mascarar_telefone("3244-1010") == "[TELEFONE-REMOVIDO]"
        assert mascarar_telefone("") == "[TELEFONE-REMOVIDO]"

    def test_nome_vazio_nao_levanta(self) -> None:
        assert mascarar_nome("") == "[NOME-REMOVIDO]"

    def test_repr_nunca_levanta(self) -> None:
        """Um `__repr__` que estoura dentro do logging derruba o registro inteiro."""
        assert "?" in repr(Lead())
        assert "?" in repr(Lead(nome_cifrado=b"lixo", telefone_cifrado=b"lixo"))


class TestDescuidoMaisComum:
    def test_repr_do_lead_ja_sai_protegido(self) -> None:
        lead = Lead(nome_cifrado=cifrar("Tarcísio Nóbrega"), telefone_cifrado=cifrar(TELEFONE))
        texto = repr(lead)
        assert "(83) *****-4471" in texto
        assert "Tarcísio N." in texto
        assert "988714471" not in texto
        assert "Nóbrega" not in texto


def test_fixo_sem_ddd_continua_sendo_redigido() -> None:
    """A restrição do olhar-atrás não pode ter aberto buraco no que já protegia."""
    for texto in (
        "me liga no 3244-1010",
        "3244-1010",
        "loja: 3244 1010, falar com o Raí",
        "(83) 3244-1010",
    ):
        assert "3244" not in redigir(texto), texto


def test_numero_de_documento_nao_e_confundido_com_telefone() -> None:
    """Regressão de um caso real: o Raí lia "espelho SV-[TELEFONE-REMOVIDO]" na tela."""
    assert redigir("espelho SV-2026-0001 emitido") == "espelho SV-2026-0001 emitido"
    assert redigir("chassi 9BWZZZ377VT004471") == "chassi 9BWZZZ377VT004471"
