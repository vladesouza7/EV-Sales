# ADR-008 — OpenRouter como provedor de LLM

**Status:** aceito
**Data:** 2026-09-03

---

## Contexto

A Aurora precisa de um modelo que faça três coisas simultaneamente: conversar bem em português
brasileiro coloquial (o cliente escreve "queria um carrinho pra rodar na cidade"), **chamar tools
com confiabilidade** (é assim que preço e estoque chegam, por [ADR-003](ADR-003-numeros-nunca-saem-do-modelo.md)),
e custar pouco o suficiente para caber em R$ 0,45 por conversa.

Há uma restrição de projeto que pesa tanto quanto as três: o [ADR-006](ADR-006-observabilidade-e-teto-de-custo.md)
exige **custo real por conversa**, faturado e não estimado. E há uma restrição de portfólio: um
agente que só funciona com um provedor é um agente que envelheceu antes de nascer.

## Decisão

**OpenRouter como gateway único, atrás de uma interface `ProvedorLLM` do EV-Sales.**

```
    Aurora (agente)
          │
          ▼
   ProvedorLLM  ← interface do EV-Sales: mensagens, tools, streaming, usage
          │
   OpenRouterLLM  ← única implementação no v1
          │
   ┌──────┴───────┬─────────────┐
   ▼              ▼             ▼
 modelo         fallback     fallback
 primário          1            2
```

**Configuração fixada no `.env`, não escolhida em tempo de execução pelo agente:**

| Papel | Escolha |
|---|---|
| Modelo primário da conversa | um modelo rápido e barato de tool calling, fixado por id exato e versão |
| Fallback | dois modelos de fabricantes diferentes, na ordem declarada |
| Roteamento de dados | apenas provedores com retenção zero e sem treino sobre os dados |
| Temperatura | baixa — a Aurora conversa, não escreve poesia |
| Contabilidade | campo `usage` da resposta, gravado no span do turno |

Três pontos que não são detalhe:

**1. Modelo fixado por versão, nunca por alias.** Um alias que aponta para "o mais novo" muda a
qualidade da Aurora sem um commit meu. Atualizar modelo é um PR, com o eval do
[S-03](../spec/S-03-agente-aurora.md) rodando antes.

**2. A restrição de provedores é o que torna o OpenRouter aceitável.** Sem isso, a conversa do
cliente poderia cair num provedor que retém prompt — e [ADR-007](ADR-007-pii-cifrada-e-mascarada.md)
não sobreviveria. A preferência de roteamento é configuração de código, versionada e revisável.

**3. Créditos pré-pagos, não cartão com limite aberto.** É a última barreira física do teto do
Raí: mesmo que o corte por Redis falhe, a conta simplesmente para. Defesa em profundidade contra
o medo nº 6.

## Alternativas consideradas

### API direta de um único fabricante — **descartada**

Menos uma camada, latência um pouco menor e a relação comercial direta com quem faz o modelo.
Descartada por **acoplamento**: o EV-Sales precisa sobreviver a um modelo ficando caro, sendo
descontinuado ou piorando numa versão nova. Com um provedor só, trocar é reescrever a camada de
integração no meio de um incidente.

Havia também um motivo de portfólio, e vale admitir: com OpenRouter eu troco o modelo primário
mudando uma variável de ambiente, o que torna o eval comparativo entre modelos uma tarde de
trabalho em vez de uma refatoração.

O contra-argumento honesto é que o OpenRouter adiciona um ponto de falha que não seria meu nem
do fabricante. Mitigado pela lista de fallback e pela interface `ProvedorLLM` — se o OpenRouter
cair de vez, escrever uma implementação direta é uma tarde.

### Ollama local — **descartada, e foi a decisão mais disputada**

Argumentos fortes, e nenhum deles é fraco: custo marginal zero por token, PII que não sai da
máquina (resolveria boa parte do [ADR-007](ADR-007-pii-cifrada-e-mascarada.md) por eliminação), e
independência total de terceiros.

Perdeu por três razões, em ordem de peso:

1. **Tool calling.** É o mecanismo que sustenta o [ADR-003](ADR-003-numeros-nunca-saem-do-modelo.md).
   Modelos que rodam em hardware de loja erram mais na escolha de tool e na formatação de
   argumentos — e cada erro desses é a Aurora respondendo sem consultar o estoque, que é
   exatamente o risco que o projeto existe para eliminar.
2. **O requisito de custo vira infra.** O Raí quer saber quanto custa cada atendimento. Com Ollama
   a resposta é "uma GPU amortizada", que não responde a pergunta dele nem permite comparar
   versões do prompt.
3. **Português coloquial com gíria paraibana.** "Um carro pra ir e voltar de Cabedelo" precisa
   virar qualificação estruturada. Modelos menores degradam justamente aqui.

Fica registrado como caminho de v2 para uma tarefa específica: **classificação de lead e
sumarização**, que são chamadas de baixo risco, sem tool calling e de alto volume — as boas
candidatas a rodar local.

### Dois provedores em paralelo, escolhendo por tarefa — **descartada para o v1**

Modelo caro para a conversa, barato para classificação. A economia é real, e a complexidade
também: dois caminhos de erro, dois formatos de tool call, dois evals. O OpenRouter permite fazer
isso depois trocando um id por chamada — então a porta fica aberta sem custo hoje.

## Consequências

**Aceitas:**

- Uma camada a mais na rota: OpenRouter adiciona latência e um ponto de falha. Mitigado por
  fallback declarado e medido no p95 do [S-08](../spec/S-08-observabilidade-e-custo.md).
- Há um pequeno acréscimo sobre o preço do fabricante. Contra o custo de reescrever integração
  sob pressão, é barato.
- A conversa do cliente passa por infraestrutura de terceiro. Mitigado pelo redator determinístico
  e pela restrição de roteamento — não eliminado, e está escrito.
- Depender de créditos pré-pagos significa que acabar o crédito derruba a Aurora. É o mesmo
  comportamento do teto: degrada para humano, não fatura surpresa.

**Ganhas:**

- Trocar de modelo é uma variável de ambiente e um eval, não uma refatoração.
- Custo real por conversa, do faturamento, atendendo direto a fala 6 do Raí.
- Fallback entre fabricantes diferentes: um provedor fora do ar não derruba o atendimento.
