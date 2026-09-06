# ADR-012 — O provedor de LLM vira configuração, e o custo passa a dizer se é faturado

**Status:** aceito
**Data:** 2026-09-06
**Estende:** [ADR-008](ADR-008-openrouter-como-provedor.md) — que continua valendo como escolha padrão
**Toca em:** [ADR-006](ADR-006-observabilidade-e-teto-de-custo.md), [ADR-007](ADR-007-pii-cifrada-e-mascarada.md)

---

## Contexto

O [ADR-008](ADR-008-openrouter-como-provedor.md) escolheu o OpenRouter como gateway único e
escreveu, nas próprias consequências, a promessa que este ADR cobra:

> "se o OpenRouter cair de vez, escrever uma implementação direta é uma tarde."

A tarde chegou por outro motivo, e é bom que o motivo esteja escrito: **a Sol & Volt não deveria
depender de uma conta em um intermediário para o sistema funcionar.** Crédito pré-pago que acaba
num sábado, conta suspensa, mudança de termos — são três formas de a Aurora parar por algo que não
é o código nem o modelo. Some a isso que o Raí já opera com hardware na loja, e que a v2 prevista
no 008 — classificação e sumarização rodando local — precisa de um caminho para existir.

Há ainda um argumento que o 008 registrou como o mais forte do lado descartado: com o modelo
rodando na própria máquina, **a conversa do cliente não sai da infraestrutura da Sol & Volt** —
o que elimina, por construção, a superfície que o [ADR-007](ADR-007-pii-cifrada-e-mascarada.md)
só consegue mitigar.

## Decisão

**O provedor passa a ser configuração no `.env`, não escolha de código. O contrato `ProvedorLLM`
não muda, e `EVSALES_PROVEDOR=openrouter` continua sendo o padrão.**

Não são quatro integrações. **Ollama, NVIDIA NIM e Gemini expõem endpoint compatível com a API da
OpenAI**, com tool calling no mesmo formato — o mesmo `/chat/completions`, os mesmos `tools`, o
mesmo `tool_calls` de volta. O que muda entre eles é URL base, header de autenticação e os campos
extras que só um deles entende. Isso é tabela de configuração, não hierarquia de classes:

| Provedor | Endpoint | Chave | Custo faturado |
|---|---|---|---|
| `openrouter` | `openrouter.ai/api/v1` | obrigatória | **sim** — campo `usage.cost` |
| `ollama` | `localhost:11434/v1` | não usa | não — **e é zero de verdade** |
| `gemini` | `generativelanguage.googleapis.com/v1beta/openai` | obrigatória | não |
| `nvidia` | `integrate.api.nvidia.com/v1` | obrigatória | não |
| `compativel` | `EVSALES_LLM_URL`, livre | opcional | não |

`compativel` existe para não precisar de um ADR novo a cada provedor que apareça falando o mesmo
protocolo. É a única porta aberta, e ela é explícita.

### A parte que não é conveniência: o custo

O [ADR-006](ADR-006-observabilidade-e-teto-de-custo.md) exige o custo **faturado**, não estimado —
"o número gravado é o número que o Raí paga" —, e recusou explicitamente tokenizer local e tabela
de preço copiada à mão. Só o OpenRouter devolve `cost`. Nos demais vem contagem de token, e
converter token em real exige justamente a tabela que o 006 recusou.

Gravar `0` nos três casos seria a pior saída possível: o teto de R$ 900 pararia de proteger **em
silêncio**, e o painel mostraria R$ 0,00 para uma API que está sendo paga. Então:

**A trilha passa a gravar se o custo é faturado.** `trilha.custo_faturado` é `boolean NOT NULL`.
Turno de provedor que não fatura entra com `custo_micro_reais = 0` e `custo_faturado = false`, e o
mês que tiver qualquer turno assim registra o incidente `custo_nao_faturado` — uma vez por dia, na
revisão semanal do Raí.

A diferença que a coluna guarda é a diferença entre **"custou zero"** e **"não sei quanto custou"**.
No Ollama as duas coincidem; no Gemini e na NVIDIA, não.

### A parte que não é conveniência: a PII

O `provider: {data_collection: deny}` que torna o OpenRouter aceitável sob o
[ADR-007](ADR-007-pii-cifrada-e-mascarada.md) é campo dele, e não existe nos outros. A restrição de
roteamento continua sendo enviada quando o provedor é o OpenRouter, e **cada provedor novo entra
com a sua política lida e escrita nesta tabela** — não por omissão:

| Provedor | O que acontece com a conversa |
|---|---|
| `openrouter` | Roteamento restrito a provedores com retenção zero e sem treino sobre os dados |
| `ollama` | **Não sai da máquina.** Elimina a superfície em vez de mitigá-la |
| `gemini` / `nvidia` | Sujeita aos termos do fabricante — verificar antes de ir a produção com cliente real |

A redação da [S-09 §4](../spec/S-09-protecao-de-pii.md) continua rodando antes do prompt em todos
os casos, e o telefone continua não entrando em nenhum.

## Alternativas consideradas

### Uma classe por fabricante — **descartada**

Era o desenho óbvio: `OllamaLLM`, `GeminiLLM`, `NvidiaLLM`, cada uma com o seu cliente. Descartada
porque as três falam o mesmo protocolo: seriam três cópias do mesmo `POST`, divergindo com o tempo
em coisas que não são diferença real. O que muda de fato — URL, header, campos extras, se fatura —
cabe em cinco linhas de tabela, e uma tabela é lida de uma vez.

Se algum dia entrar um provedor que **não** fale o protocolo da OpenAI, aí sim nasce uma segunda
implementação do `ProvedorLLM`. O contrato existe para isso.

### Deixar o custo sempre como `0` e resolver depois — **descartada**

Era o caminho mais curto, e o mais perigoso deste ADR. O teto de custo é resposta direta à fala 6
do Raí — "não quero descobrir dia 30 que gastei quatro mil real". Um teto que deixa de contar sem
avisar é pior que teto nenhum: teto nenhum ele sabe que não tem.

### Cotação e tabela de preço por modelo, para estimar o custo dos outros — **descartada**

Daria um número no painel para todos os provedores. Recusada porque seria **o número errado com
cara de certo** — exatamente o que o [ADR-003](ADR-003-numeros-nunca-saem-do-modelo.md) proíbe para
o cliente e o [ADR-006](ADR-006-observabilidade-e-teto-de-custo.md) proíbe para o Raí. Preferimos
"não sei quanto custou", que é verdade, a "R$ 3,40", que é chute formatado.

### Trocar o padrão para Ollama — **descartada, por enquanto**

Custo marginal zero e PII que não sai da loja continuam sendo os melhores argumentos do projeto.
O que segura é o mesmo do 008: **tool calling** é o mecanismo que sustenta o
[ADR-003](ADR-003-numeros-nunca-saem-do-modelo.md), e modelo que erra a escolha da tool é a Aurora
respondendo sem consultar o estoque.

**Gatilho de revisão, com número:** o padrão muda para Ollama quando um modelo local passar as três
suítes-portão da [S-03 §8](../spec/S-03-agente-aurora.md) — preço, autonomia e injection — com
**100%**, no mesmo eval que o modelo do OpenRouter roda. Sem eval, trocar é troca no escuro.

## Consequências

**Aceitas:**

- Mais uma variável de ambiente e mais uma coluna. A coluna é o preço de o painel não mentir.
- Com provedor que não fatura, o teto de R$ 900 **não protege** — e passa a dizer isso, num
  incidente que aparece na revisão, em vez de num zero silencioso.
- A qualidade da Aurora passa a depender de configuração de operação. Mitigado pelo eval da
  [S-03 §8](../spec/S-03-agente-aurora.md), que é quem tem autoridade para aprovar uma troca — e
  que ainda não existe. **Até ele existir, trocar de provedor é decisão sem rede.**
- `compativel` é uma porta aberta: aponta para qualquer URL. É superfície de operação, e por isso
  o `.env.example` está na tabela de revisão humana obrigatória do [CLAUDE.md](../../CLAUDE.md).

**Ganhas:**

- A Sol & Volt deixa de depender de uma conta em intermediário para o sistema funcionar.
- O caminho para a v2 prevista no [ADR-008](ADR-008-openrouter-como-provedor.md) existe sem
  refatoração.
- Comparar dois provedores na mesma conversa gravada vira troca de variável, como já era com dois
  modelos.
- O painel de custo passa a distinguir "custou zero" de "não sei quanto custou" — informação que
  ele não tinha nem com o OpenRouter sozinho.
