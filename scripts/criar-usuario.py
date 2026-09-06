#!/usr/bin/env python
"""Cria um usuário do EV-Sales (S-11 §8).

    uv --project backend run python scripts/criar-usuario.py

Pede nome, e-mail, perfil e senha. **A senha não é impressa, não vai para o histórico do
shell e não fica em argumento de linha de comando** — por isso é interativo, e não
`--senha=...`, que ficaria no `~/.bash_history` de quem rodou.

Não há convite por e-mail porque não há servidor de e-mail no compose (S-10 §1). São
quatro pessoas: o Raí cria, e quem esqueceu a senha pede para ele rodar isto de novo.
"""

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import select  # noqa: E402

from app.autenticacao import SENHA_MINIMA, cifrar_senha, criar_usuario  # noqa: E402
from app.db import Sessao, agora  # noqa: E402
from app.modelos import Usuario, Vendedor  # noqa: E402

PERFIS = ("dono", "gerente", "vendedor")


def main() -> int:
    nome = input("Nome: ").strip()
    email = input("E-mail: ").strip().lower()
    perfil = input(f"Perfil {PERFIS}: ").strip()
    if perfil not in PERFIS:
        print(f"Perfil precisa ser um de {PERFIS}.")
        return 1

    with Sessao() as sessao:
        vendedor_id = None
        if perfil == "vendedor":
            vendedores = list(sessao.scalars(select(Vendedor).order_by(Vendedor.nome)))
            if not vendedores:
                print("Não há vendedores cadastrados. Rode o seed antes.")
                return 1
            for numero, vendedor in enumerate(vendedores, 1):
                print(f"  {numero}. {vendedor.nome}")
            escolha = int(input("Qual vendedor? ").strip())
            vendedor_id = vendedores[escolha - 1].id

        existente = sessao.scalars(select(Usuario).where(Usuario.email == email)).one_or_none()
        senha = getpass.getpass("Senha: ")
        if len(senha) < SENHA_MINIMA:
            print(f"A senha precisa de pelo menos {SENHA_MINIMA} caracteres.")
            return 1
        if senha != getpass.getpass("Repita a senha: "):
            print("As senhas não conferem.")
            return 1

        if existente is not None:
            # Trocar a senha derruba as sessões daquela pessoa — é como o Raí resolve
            # "esqueci a senha" e também "perdi o celular" (S-11 §8).
            existente.senha_hash = cifrar_senha(senha)
            existente.senha_trocada_em = agora()
            existente.sessoes_validas_apos = agora()
            existente.tentativas_falhas = 0
            existente.bloqueado_ate = None
            existente.ativo = True
            sessao.commit()
            print(f"\nSenha de {existente.nome} trocada. As sessões dela foram encerradas.")
            return 0

        usuario = criar_usuario(
            sessao, nome=nome, email=email, senha=senha, perfil=perfil, vendedor_id=vendedor_id
        )
        print(f"\n{usuario.nome} criado como {usuario.perfil}.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
