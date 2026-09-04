# ADR-002 — pgvector no v1, Qdrant só quando o catálogo justificar

**Status:** aceito · **reavaliado em 2026-09-03** — decisão mantida, **gatilho corrigido**
**Data:** 2026-09-03

> **Reavaliação, e uma correção que interessa mais que a decisão.**
>
> Escrevi este ADR com uma estimativa de 15 modelos e um gatilho de revisão de **60 modelos**. O
> levantamento de mercado ([MARCAS-E-MODELOS-BR](../pesquisa/MARCAS-E-MODELOS-BR.md)) trouxe o
> número real: **18 marcas e ~58 modelos** — 97% do gatilho.
>
> Quase migrei. Aí olhei as duas métricas lado a lado:
>
> | | Limite | Hoje | Consumido |
> |---|---|---|---|
> | Modelos no catálogo | 60 | 58 | **97%** |
> | Chunks no corpus | 20.000 | ~2.800 | **14%** |
>
> Uma métrica gritando e a outra dormindo, medindo o mesmo sistema. Isso não é um sinal para migrar:
> **é a prova de que a métrica de modelos era um proxy ruim.** Contagem de modelos não move o custo
> de busca vetorial — quem move é volume de chunks e latência. Um catálogo de 58 modelos com uma
> ficha técnica curta cada um gera menos texto que 10 modelos com manual inteiro indexado.
>
> Obedecer a esse gatilho teria me feito subir um Qdrant para operar a 14% da capacidade do que já
> tenho. **Corrigi a métrica em vez de obedecê-la**, e o gatilho novo está na Decisão. A lição fica
> registrada porque ela vale mais que este ADR: um limite que dispara enquanto a métrica causal está
> a 14% é um limite que teria custado um serviço inteiro por nada.

---

## Contexto

O rascunho inicial de arquitetura ([docs/pesquisa](../pesquisa/RASCUNHO-ARQUITETURA-INICIAL.md))
previa Qdrant como banco vetorial dedicado, carregando catálogo semântico, manuais, FAQs e
comparativos.

O número que derruba isso: **o catálogo tem 18 marcas e ~58 modelos**, e a loja mantém de 14 a 19
unidades em estoque. O corpus semântico inteiro do negócio — descrição dos modelos, objeções
frequentes, política de garantia, informação de carregamento, comparativos entre marcas — cabe em
algo perto de **2.800 chunks**.

Para dar escala: pgvector com índice HNSW atende busca por similaridade em corpus dessa ordem em
poucos milissegundos, numa instância modesta. O ponto em que um banco vetorial dedicado começa a
ganhar de verdade está uma ou duas ordens de grandeza acima.

Qdrant é excelente e resolve um problema que a Sol & Volt não tem — **ainda**.

## Decisão

**Extensão `pgvector` no mesmo Postgres do ADR-001.** Sem serviço vetorial dedicado no v1.

### Gatilho de revisão — versão corrigida

Só entram aqui métricas que **causam** a dor, medidas em produção:

| Gatilho | Limite | Hoje | Tipo |
|---|---|---|---|
| **p95 da busca semântica** | 200 ms | a medir | causal — é o que o cliente sente |
| **Chunks no corpus** | 20.000 | ~2.800 (14%) | causal — é o que o índice carrega |
| Modelos no catálogo | — | 58 | **acompanhamento, não gatilho** |

Qualquer um dos dois primeiros reabre este ADR. Contagem de modelos **deixou de ser gatilho**: ela é
um proxy ruim, como a reavaliação no topo demonstrou. Fica no painel porque um salto grande nela
antecipa crescimento de corpus — mas quem decide é o corpus e a latência, medidos.

O p95 é o gatilho principal porque é o único que corresponde a algo que alguém percebe. Ele é medido
desde o primeiro dia em [S-08](../spec/S-08-observabilidade-e-custo.md), o que torna esta revisão
automática em vez de depender de eu lembrar de olhar.

Migrar de pgvector para Qdrant depois é trabalho de um dia — o próprio Qdrant documenta esse
caminho. Operar um serviço a mais desde o primeiro dia é trabalho todo dia.

## Alternativas consideradas

### Qdrant desde o v1 — **descartada**

O argumento a favor era preparar a arquitetura para crescimento. Descartei porque "preparado
para crescer" só é virtude quando o crescimento é previsível, e aqui ele não é: o catálogo cresce
aos saltos, quando uma marca nova entra no país — e pode ficar parado por anos entre um salto e
outro.

O custo real, em contrapartida, é imediato e todo dia: mais um container para a equipe do Raí
subir, mais um backup, e — o que mais pesa — a **consistência entre dois armazenamentos**.
Quando o Seal `…4471` é vendido, o Postgres sabe na mesma transação; o índice vetorial só sabe
quando alguém reindexar. Enquanto isso, a Aurora pode recuperar semanticamente um carro que não
existe mais. Sincronizar isso é um worker, uma fila e uma classe de bug que só aparece em
produção.

Com pgvector, o embedding e a linha do estoque estão na mesma transação. O problema não é
resolvido — ele deixa de existir.

### Sem busca vetorial, só SQL — **descartada**

Tentador, e quase certo. `WHERE preco <= X AND autonomia >= Y` cobre a maior parte das consultas
da Aurora, e é assim que preço e estoque são consultados de qualquer forma ([ADR-003](ADR-003-numeros-nunca-saem-do-modelo.md)).

Mas a Persona 2 pergunta *"dá pra ir pra Recife com esse?"*, e a Persona 1 pergunta *"é bom pra
quem nunca teve elétrico?"*. Essas consultas não viram `WHERE`. Elas batem em texto de objeção
e de política — e é aí que a busca semântica ganha o lugar dela: **texto explicativo, nunca
número**. A busca vetorial não decide preço nem disponibilidade.

### RAG sobre manuais em PDF — **adiada para o v2**

Está no [PRD, seção 5](../PRD.md#5-o-que-fica-de-fora--e-por-quê). Além do custo de pipeline, cada
PDF indexado é superfície nova de alucinação: um manual de 2024 diz coisa diferente do modelo 2026,
e a Aurora não tem como saber qual está lendo.

Com 58 modelos, essa alternativa ficou mais tentadora do que era com 15 — curar conteúdo à mão para
58 modelos é trabalho de verdade. Mantenho a recusa, com uma concessão de escopo: a curadoria manual
cobre **as objeções, a política e o carregamento** (que valem para todos os modelos), e a ficha
técnica por modelo vem estruturada no Postgres, não de PDF. O que não tem número confirmado entra
como `NULL`, e a Aurora diz que vai confirmar — nunca estima.

## Consequências

**Aceitas:**

- Busca vetorial em Postgres é mais lenta que em Qdrant. Em ~2.800 chunks, a diferença é invisível
  para o cliente e o teste de latência do [S-08](../spec/S-08-observabilidade-e-custo.md) protege
  contra a regressão.
- Se a Sol & Volt abrir mais lojas e o catálogo explodir, haverá uma migração pela frente. É um
  custo futuro condicional, contra um custo presente certo.
- O corpus semântico é curado à mão. Não escala, e nesta dimensão ainda é vantagem: alguém leu
  cada frase que a Aurora pode recuperar.

**Ganhas:**

- Um container a menos, um backup a menos, uma fila de sincronização a menos.
- Embedding e estoque na mesma transação: impossível recomendar semanticamente um carro vendido.
