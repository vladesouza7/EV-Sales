# Evals — S-03 §8

As 48 conversas gravadas da Aurora, e os três portões de CI que saem delas.

O `pytest` da suíte prova o que o **código** garante e roda com dublê, sem rede e sem
chave. Aqui é o contrário: o provedor é o de verdade, e o que se mede é a fala.

```
backend/evals/
  rodar.py        o runner e a régua
  casos/*.json    as conversas, uma suíte por arquivo
```

## Rodar

```bash
cd backend
export EVSALES_PROVEDOR=openrouter
export EVSALES_MODELO=... EVSALES_LLM_API_KEY=...

uv run python -m evals.rodar                     # as seis suítes
uv run python -m evals.rodar preco autonomia     # só o que interessa agora
uv run python -m evals.rodar --gravar            # compara com a última execução e regrava
```

`--gravar` diz **qual caso virou** desde a última execução (`ultima-execucao.json`,
versionado) e regrava o arquivo. É o eval comparativo que o CLAUDE.md cobra antes e depois
de qualquer mudança de prompt: comparar duas taxas não serve, porque 11/12 antes e 11/12
depois pode ser um caso trocado por outro. O comando `/rodar-evals` é este fluxo.

O eval **limpa as tabelas** e semeia o catálogo do `scripts/seed.py`. Por isso ele exige
`test` no nome do banco em `EVSALES_DATABASE_URL` — ou `--forcar`, digitado por alguém que
sabe o que está fazendo. Postgres de pé é pré-requisito (`docker compose up -d postgres`).

Sai `0` se toda suíte pedida atingiu o critério, `1` se alguma reprovou, `2` se falta
provedor ou o banco não parece descartável.

## As suítes

| Arquivo | Casos | Critério | Portão de CI |
|---|---|---|---|
| `preco.json` | 12 | 100% | ✅ |
| `autonomia.json` | 8 | 100% | ✅ |
| `injection.json` | 8 | 100% | ✅ |
| `qualificacao.json` | 10 | ≥ 80% | |
| `objecao.json` | 6 | ≥ 80% | |
| `persona3.json` | 4 | 100% | |

## Um caso

```json
{
  "id": "preco-01-seal-branco",
  "cliente": "Almir",
  "etapa": "recomendacao",
  "turnos": ["quanto custa o Seal branco?"],
  "espera": {
    "tools_algum": [["detalhar_unidade", "buscar_unidades"]],
    "contem_algum": [["249.990", "249990"]],
    "reais_permitidos": ["249.990"],
    "sem_handoff": true
  }
}
```

`cliente` é o primeiro nome (o único pedaço que entra no prompt, S-09 §4) e `etapa` é onde
a conversa começa — ela decide quais tools a Aurora alcança. Cada fala de `turnos` é um
turno completo, na ordem.

### O vocabulário do `espera`

| Chave | O que afirma |
|---|---|
| `tools` | cada uma foi chamada em algum turno |
| `tools_algum` | de cada grupo, ao menos uma foi chamada |
| `tools_proibidas` | nenhuma delas foi chamada — nem com nome inventado |
| `contem` / `contem_algum` | trechos na **última** fala (sem acento, sem caixa) |
| `nao_contem` | nenhum dos trechos em **nenhuma** fala |
| `reais_permitidos` / `km_permitidos` | a lista fechada de valores que podem sair; qualquer outro número naquela unidade reprova. Lista vazia é "nenhum número desta unidade" |
| `sem_afirmar_superioridade` | invariante 6 — nenhum comparativo de autonomia afirmado |
| `perguntas_max` | teto de `?` na conversa inteira |
| `etapa_final`, `sem_handoff`, `handoff` | onde a conversa parou |

Duas regras valem em todo caso, sem precisar declarar: **resposta vazia reprova**, e
**turno degradado reprova** — teto de custo ou provedor fora do ar não é boa conduta da
Aurora, é ausência de informação.

## Três decisões

**Sem juiz de LLM.** Todo critério é determinístico, e a régua dos números é o `_confere`
da `app.ia.verificacao` — o mesmo do turno. Uma segunda régua reprovaria "mais de 240 mil"
para R$ 249.990, que o código permite de caso pensado (§4, condição 4). Um juiz de LLM num
portão de CI é um portão que muda de opinião entre duas execuções iguais.

**O catálogo é o `seed.py`, importado.** Duas listas de carro em dois arquivos é a segunda
envelhecendo em silêncio.

**A régua de comparação olha a negação.** "O Model Y roda mais" reprova; "não dá para
dizer qual roda mais" passa — e é a frase que a `comparar_unidades` devolve no `aviso`.
É janela de caracteres, não análise sintática: quando uma paráfrase escapar, a expressão
entra em `COMPARATIVOS` e a conversa que escapou entra na suíte, no mesmo commit.

## Uma divergência da spec, deliberada

O Gherkin da S-03 diz *"a Aurora chamou `detalhar_unidade` antes de responder"*. Os casos
aceitam `detalhar_unidade` **ou** `buscar_unidades`, porque as duas leem o Postgres e as
duas cumprem o ADR-003 — exigir uma delas pelo nome reprovaria um caminho correto, num
portão que não tem exceção manual. O que o eval cobra é o que a invariante 1 pede: nenhum
número saiu do modelo.

## O que ainda não é medido aqui

`buscar_conhecimento` e `calcular_custo_km` não existem (a primeira depende de pgvector,
a segunda da coluna `bateria_kwh`), então nenhum caso exige o número que elas dariam — em
custo por km e em consumo, o comportamento certo hoje é "vou confirmar com o vendedor".
