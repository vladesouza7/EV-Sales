#!/usr/bin/env python
"""Sobe fotos de unidade para o MinIO e aponta `unidades.foto_url` para elas (ADR-013).

    uv --project backend run python scripts/subir-fotos.py fotos/*.jpg

O nome do arquivo é o chassi: `9BWZZZ377VT100001.jpg` vira a foto daquela unidade. É
assim porque a Sol & Volt vende chassi, não modelo — dois Seal brancos do mesmo ano são
carros diferentes, com quilometragem e preço diferentes, e cada um tem a sua foto.

ponytail: um script, não uma tela de upload. A tela precisa de login, que o projeto ainda
não tem, e é entrega da S-04. Isto já resolve o caso real de hoje: o Tarcísio manda as
fotos e alguém sobe.
"""

import mimetypes
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.arquivos import caminho_da_foto, garantir_bucket, guardar  # noqa: E402
from app.db import Sessao  # noqa: E402
from app.modelos import Unidade  # noqa: E402


def main(caminhos: list[str]) -> int:
    if not caminhos:
        print(__doc__)
        return 1

    garantir_bucket()
    enviadas = 0
    with Sessao() as sessao:
        for bruto in caminhos:
            arquivo = Path(bruto)
            chassi = arquivo.stem
            unidade = sessao.get(Unidade, chassi)
            if unidade is None:
                print(f"  ✗ {arquivo.name}: nenhuma unidade com o chassi {chassi}")
                continue

            tipo = mimetypes.guess_type(arquivo.name)[0] or "application/octet-stream"
            caminho = caminho_da_foto(arquivo.name)
            guardar(caminho, arquivo.read_bytes(), tipo)
            # A URL é a rota do app, não a do MinIO: o bucket é privado e continua sem
            # porta publicada.
            unidade.foto_url = f"/fotos/{arquivo.name}"
            enviadas += 1
            print(f"  ✓ {unidade.marca} {unidade.modelo} {unidade.cor} → /fotos/{arquivo.name}")
        sessao.commit()

    print(f"\n{enviadas} foto(s) no ar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
