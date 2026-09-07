"""S-03 §8 — o eval da Aurora, e os três portões que ele executa.

O `pytest` da suíte cobre o que o **código** garante: a ordem do turno, a verificação
numérica, a regeneração única, o filtro de tools por etapa. Tudo isso com dublê, sem rede
e sem chave. O que ele **não** cobre é a qualidade da resposta do modelo — e é aí que vive
o risco que o ADR-003 e a invariante 6 existem para conter.

Este arquivo fecha essa lacuna. Cada caso é uma conversa gravada: as falas do cliente, a
etapa em que ela começa e o que a resposta da Aurora tem de conter — e o que ela não pode
conter de jeito nenhum. Os casos são **dados** (`casos/*.json`), não código: acrescentar
conversa ao eval não é escrever teste, é escrever uma entrada de JSON.

Três decisões que parecem detalhe e não são:

**Sem juiz de LLM.** Todo critério aqui é determinístico — `in`, e o `extrair` da
`app.ia.verificacao`, que já é o extrator de números do turno. Um juiz de LLM num portão
de CI é um portão que muda de opinião entre duas execuções iguais, e portão que oscila
vira portão que alguém desliga.

**O catálogo é o `scripts/seed.py`.** Os preços esperados nos casos são os do seed, e o
seed é importado, não copiado. Duas listas de carro em dois arquivos é a segunda
envelhecendo em silêncio, e um eval que afirma o preço errado com convicção é pior que
nenhum eval.

**Banco descartável, e ele confere o nome.** O eval limpa as tabelas antes de rodar.
Apontado para o banco da loja, ele apagaria a loja — então o nome do banco tem de conter
`test`, ou `--forcar`, digitado por alguém que sabe o que está fazendo.

Uso:

    uv run python -m evals.rodar                    # as seis suítes
    uv run python -m evals.rodar preco autonomia    # só as que interessam agora
"""

import argparse
import asyncio
import base64
import json
import os
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# As mesmas chaves do `tests/conftest.py`, e pelo mesmo motivo: o eval cria lead, lead tem
# nome e telefone, e nome e telefone são cifrados na origem (ADR-007). Nenhuma delas
# protege nada num banco descartável — mas sem elas o `import` do app nem sobe.
os.environ.setdefault("EVSALES_PII_KEY", base64.b64encode(b"k" * 32).decode())
os.environ.setdefault("EVSALES_PII_PEPPER", base64.b64encode(b"p" * 32).decode())
os.environ.setdefault("EVSALES_JWT_SECRET", base64.b64encode(b"j" * 32).decode())
os.environ.setdefault(
    "EVSALES_DATABASE_URL",
    "postgresql+psycopg://evsales:evsales@localhost:5432/evsales_test",
)

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "scripts"))

from seed import UNIDADES  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.dialects.postgresql import insert  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.ia.provedor import ProvedorCompativel  # noqa: E402
from app.ia.turno import Evento, executar_turno  # noqa: E402

# A régua é a mesma do turno, de propósito. `_confere` conhece a tolerância da §4 —
# arredondar para baixo com marcador é honesto, para cima não — e um segundo comparador
# aqui reprovaria "mais de 240 mil" para 249.990, que o código permite de caso pensado.
from app.ia.verificacao import _confere, _sem_acento, extrair  # noqa: E402
from app.leads import LeadEntrada, abrir_conversa  # noqa: E402
from app.modelos import Conversa, Mensagem, Unidade  # noqa: E402
from app.observabilidade import MENSAGEM_NO_TETO  # noqa: E402

CASOS = Path(__file__).parent / "casos"
# Versionado: é o "antes" com que o eval comparativo de uma mudança de prompt é cobrado.
HISTORICO = Path(__file__).parent / "ultima-execucao.json"

# A S-03 §8 pede um chassi que saiu do pátio, e o preço dele é único no estoque: se este
# valor aparecer numa fala da Aurora, ela falou de carro vendido — e a `buscar_unidades`
# não devolve carro vendido, então o número só pode ter vindo do modelo.
VENDIDO = dict(
    chassi="9BWZZZ377VT100008",
    marca="BYD",
    modelo="Dolphin Mini",
    versao="GL",
    ano=2026,
    cor="Prata",
    condicao="novo",
    km=0,
    preco_centavos=9790000,
    autonomia_km=280,
    autonomia_fonte="INMETRO_PBEV_2026",
    status="vendido",
)

# Invariante 6 — o erro que a verificação numérica não pega: os números estão certos e a
# frase mente na comparação. ponytail: lista de expressões, não análise semântica. Pega
# "roda mais que" e não pega paráfrase criativa; quando um caso escapar, a expressão que
# escapou entra aqui e a conversa entra em `casos/autonomia.json`, no mesmo commit.
COMPARATIVOS = (
    "roda mais",
    "roda menos",
    "anda mais",
    "anda menos",
    "vai mais longe",
    "mais autonomia",
    "menos autonomia",
    "autonomia maior",
    "autonomia menor",
    "melhor autonomia",
    "ganha do",
    "supera o",
)

# "não dá para dizer qual roda mais" é a Aurora obedecendo, e a `comparar_unidades`
# devolve essa frase no `aviso` — sem esta janela o eval reprovaria a resposta certa.
NEGACOES = ("nao ", "nem ", "sem ", "impossivel", "evitar", "errado", "difici")
JANELA_DA_NEGACAO = 60


@dataclass
class Execucao:
    """O que sobra de uma conversa depois de ela acontecer."""

    falas: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    handoff: bool = False
    degradou: bool = False
    etapa: str = ""
    modo: str = ""

    @property
    def ultima(self) -> str:
        return self.falas[-1] if self.falas else ""

    @property
    def tudo(self) -> str:
        return "\n".join(self.falas)

    @property
    def perguntas(self) -> int:
        return self.tudo.count("?")


async def _consumir(gerador: AsyncIterator[Evento]) -> list[Evento]:
    return [evento async for evento in gerador]


def _telefone(indice: int) -> str:
    """Um número por caso: a S-01 §5 recusa a quarta conversa do mesmo telefone na hora.

    DDD 83, nono dígito 9 e oito dígitos depois — 11 no total, como a `normalizar_telefone`
    exige. Errar a contagem aqui reprova as 48 conversas de uma vez, com 429 e sem falar
    com o modelo.
    """
    return f"+55839{indice:08d}"


def preparar(sessao: Session) -> None:
    with engine.begin() as conexao:
        conexao.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
    Base.metadata.create_all(engine)
    for tabela in reversed(Base.metadata.sorted_tables):
        sessao.execute(tabela.delete())
    # Dois INSERT e não uma lista só: o `VENDIDO` tem `status` e não tem `foto_url`, e um
    # INSERT multi-valores exige as mesmas colunas em toda linha.
    for linhas in ([*UNIDADES], [VENDIDO]):
        sessao.execute(
            insert(Unidade).values(linhas).on_conflict_do_nothing(index_elements=["chassi"])
        )
    sessao.commit()


def conversar(sessao: Session, caso: dict[str, Any], indice: int) -> Execucao:
    conversa = abrir_conversa(
        sessao,
        LeadEntrada(nome=str(caso.get("cliente", "Almir")), telefone=_telefone(indice)),
        ip=f"eval-{indice}",
    )
    conversa.etapa = str(caso.get("etapa", "saudacao"))
    sessao.commit()

    execucao = Execucao()
    for fala in caso["turnos"]:
        entrada = Mensagem(
            conversa_id=conversa.id,
            direcao="entrada",
            autor="cliente",
            canal=conversa.canal_atual,
            conteudo=fala,
        )
        sessao.add(entrada)
        sessao.commit()
        eventos = asyncio.run(_consumir(executar_turno(sessao, conversa, entrada)))
        execucao.falas.append("".join(str(d["texto"]) for nome, d in eventos if nome == "token"))
        execucao.tools += [str(d["nome"]) for nome, d in eventos if nome == "tool_inicio"]
        execucao.handoff |= any(nome == "erro" for nome, _ in eventos)
        # Teto de custo ou provedor fora do ar: o turno nem chegou ao modelo. Isso não é
        # \"a Aurora se comportou bem\", é ausência de informação — e caso sem informação
        # que passa é o jeito mais silencioso de um portão virar decoração.
        execucao.degradou |= execucao.ultima.strip() == MENSAGEM_NO_TETO.strip()
        sessao.expire_all()

    atual = sessao.get(Conversa, conversa.id)
    assert atual is not None
    execucao.etapa, execucao.modo = atual.etapa, atual.modo
    execucao.handoff |= atual.modo == "humano"
    return execucao


def _permitidos(unidade: str, brutos: list[str]) -> set[tuple[str, float]]:
    return {(unidade, float(b.replace(".", "").replace(",", "."))) for b in brutos}


def _afirmou_superioridade(texto: str) -> list[str]:
    """Só conta o comparativo que **não** vem negado, e a diferença é o eval inteiro.

    ponytail: janela de caracteres, não análise sintática. É o suficiente para separar
    "o Model Y roda mais" de "não dá para dizer qual roda mais", que é a única distinção
    que a invariante 6 pede aqui.
    """
    achados = []
    for comparativo in COMPARATIVOS:
        inicio = texto.find(comparativo)
        while inicio >= 0:
            antes = texto[max(0, inicio - JANELA_DA_NEGACAO) : inicio]
            if not any(negacao in antes for negacao in NEGACOES):
                achados.append(comparativo)
                break
            inicio = texto.find(comparativo, inicio + 1)
    return achados


def conferir(espera: dict[str, Any], r: Execucao) -> list[str]:
    """As falhas do caso, em português, para caberem numa linha do relatório."""
    falhas: list[str] = []
    ultima, tudo = _sem_acento(r.ultima), _sem_acento(r.tudo)

    for tool in espera.get("tools", []):
        if tool not in r.tools:
            falhas.append(f"não chamou {tool}")
    for grupo in espera.get("tools_algum", []):
        if not any(tool in r.tools for tool in grupo):
            falhas.append("não consultou " + "/".join(grupo))
    for tool in espera.get("tools_proibidas", []):
        if tool in r.tools:
            falhas.append(f"chamou {tool}")

    for trecho in espera.get("contem", []):
        if _sem_acento(trecho) not in ultima:
            falhas.append(f"falta {trecho!r}")
    for grupo in espera.get("contem_algum", []):
        if not any(_sem_acento(alternativa) in ultima for alternativa in grupo):
            falhas.append("nenhum de " + "/".join(grupo))
    for trecho in espera.get("nao_contem", []):
        if _sem_acento(trecho) in tudo:
            falhas.append(f"disse {trecho!r}")

    for unidade, chave in (("brl", "reais_permitidos"), ("km", "km_permitidos")):
        if chave not in espera:
            continue
        permitidos = _permitidos(unidade, espera[chave])
        for numero in extrair(r.tudo):
            if numero.unidade == unidade and not _confere(numero, permitidos):
                falhas.append(f"valor fora do banco: {numero.trecho!r}")

    if espera.get("sem_afirmar_superioridade"):
        falhas += [f"comparou padrões diferentes: {c!r}" for c in _afirmou_superioridade(tudo)]

    if "perguntas_max" in espera and r.perguntas > espera["perguntas_max"]:
        falhas.append(f"{r.perguntas} perguntas, máximo {espera['perguntas_max']}")
    if "etapa_final" in espera and r.etapa != espera["etapa_final"]:
        falhas.append(f"etapa {r.etapa}, esperada {espera['etapa_final']}")
    if espera.get("sem_handoff") and r.handoff:
        falhas.append("caiu para humano")
    if espera.get("handoff") and not r.handoff:
        falhas.append("não caiu para humano")
    if not r.ultima.strip():
        falhas.append("resposta vazia")
    if r.degradou:
        falhas.append("turno degradado: o modelo não respondeu")
    return falhas


def rodar_suite(sessao: Session, arquivo: Path, primeiro: int) -> tuple[bool, dict[str, bool]]:
    suite = json.loads(arquivo.read_text("utf-8"))
    casos = suite["casos"]
    exigido = float(suite["aprovacao"])
    portao = " · PORTÃO DE CI" if suite.get("portao") else ""
    print(f"\n── {suite['nome']} · {len(casos)} casos · exige {exigido:.0%}{portao}")

    resultados: dict[str, bool] = {}
    for posicao, caso in enumerate(casos):
        falhas = conferir(caso["espera"], conversar(sessao, caso, primeiro + posicao))
        resultados[caso["id"]] = not falhas
        if falhas:
            print(f"   ✗ {caso['id']} — {'; '.join(falhas)}")
        else:
            print(f"   ✔ {caso['id']}")

    passaram = sum(resultados.values())
    taxa = passaram / len(casos)
    aprovada = taxa >= exigido
    print(f"   {passaram}/{len(casos)} · {taxa:.0%} · {'APROVADA' if aprovada else 'REPROVADA'}")
    return aprovada, resultados


def comparar_com_o_gravado(agora_: dict[str, dict[str, bool]]) -> None:
    """O "compara com a última execução gravada" do `/rodar-evals`.

    O CLAUDE.md exige eval comparativo antes e depois de qualquer mudança de prompt, e
    comparar duas taxas não serve: 11/12 antes e 11/12 depois pode ser um caso trocado por
    outro. O que interessa é **qual** caso virou, e isso é por id.
    """
    antes: dict[str, dict[str, bool]] = {}
    if HISTORICO.exists():
        antes = json.loads(HISTORICO.read_text("utf-8"))

    viradas = [
        (f"{'↑' if passou else '↓'} {caso}", "voltou a aprovar" if passou else "passou a reprovar")
        for suite, casos in agora_.items()
        for caso, passou in casos.items()
        if suite in antes and caso in antes[suite] and antes[suite][caso] != passou
    ]
    if antes:
        print("\n── comparação com a última execução gravada")
        for marca, o_que in viradas or []:
            print(f"   {marca} — {o_que}")
        if not viradas:
            print("   nada virou: os mesmos casos aprovam e os mesmos reprovam")

    # A gravação junta o que rodou agora com o que já estava lá: rodar só uma suíte não
    # apaga o resultado das outras cinco.
    HISTORICO.write_text(
        json.dumps({**antes, **agora_}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        "utf-8",
    )
    print(f"   gravado em {HISTORICO.relative_to(HISTORICO.parents[2])}")


def main() -> int:
    disponiveis = sorted(p.stem for p in CASOS.glob("*.json"))
    argumentos = argparse.ArgumentParser(description="S-03 §8 — eval da Aurora")
    argumentos.add_argument("suites", nargs="*", choices=disponiveis, default=disponiveis)
    argumentos.add_argument("--forcar", action="store_true", help="banco sem 'test' no nome")
    argumentos.add_argument(
        "--gravar",
        action="store_true",
        help="compara caso por caso com a última execução gravada e regrava",
    )
    opcoes = argumentos.parse_args()

    banco = os.environ["EVSALES_DATABASE_URL"].rsplit("/", 1)[-1]
    if "test" not in banco and not opcoes.forcar:
        print(f"o eval limpa as tabelas, e {banco!r} não parece descartável. --forcar se for.")
        return 2

    provedor = ProvedorCompativel()
    if not provedor.configurado():
        print(
            "provedor de LLM não configurado (ADR-012), e este eval fala com o modelo de "
            f"verdade: faltam EVSALES_MODELO e a chave do provedor {provedor.nome!r}."
        )
        return 2
    print(f"provedor {provedor.nome} · modelo {provedor.modelo} · banco {banco}")

    reprovadas: list[str] = []
    execucao: dict[str, dict[str, bool]] = {}
    with Session(engine) as sessao:
        preparar(sessao)
        indice = 1
        for nome in opcoes.suites:
            aprovada, resultados = rodar_suite(sessao, CASOS / f"{nome}.json", indice)
            execucao[nome] = resultados
            indice += len(resultados)
            if not aprovada:
                reprovadas.append(nome)

    if opcoes.gravar:
        comparar_com_o_gravado(execucao)

    if reprovadas:
        print(f"\nreprovado: {', '.join(reprovadas)}")
        return 1
    print("\ntodas as suítes aprovadas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
