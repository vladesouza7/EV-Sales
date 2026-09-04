# ADR-006 — Trace legível e teto de custo, desde o primeiro commit

**Status:** aceito
**Data:** 2026-09-03
**Decide sobre:** [CASE.md — falas 4 e 6 do Raí](../CASE.md#o-que-ele-disse-com-as-palavras-dele)

---

## Contexto

Duas falas do Raí caem no mesmo mecanismo:

> "Se der ruim eu quero abrir a conversa e ler. Do começo ao fim. Não quero explicação de
> engenheiro, quero ler o que foi dito."

> "Não quero descobrir dia 30 que gastei quatro mil real de inteligência artificial. Quero saber
> quanto custa, e quero um teto."

São dois públicos e dois artefatos diferentes, e confundi-los é o erro comum. O Raí quer uma
**transcrição**: quem disse o quê, em que ordem, e o que a Aurora consultou antes de responder.
Eu quero um **trace**: tokens, latência, tool calls, prompt exato, custo por turno. Um dashboard
de Grafana não serve para ele; uma transcrição bonita não serve para mim.

## Decisão

**Uma origem de dados, duas leituras. E a instrumentação entra antes do agente ficar pronto, não
depois.**

### A origem: Langfuse self-hosted

Cada conversa é uma trace, cada turno é um span, cada tool call é um span filho com argumentos e
retorno. Custo por turno é calculado a partir do `usage` que o OpenRouter devolve na resposta
([ADR-008](ADR-008-openrouter-como-provedor.md)) e gravado no span — não estimado.

**PII é mascarada na origem**, antes de sair do processo: o mascaramento acontece na camada que
monta o span, não numa configuração do Langfuse. Se eu trocar de ferramenta de observabilidade,
a proteção vai junto ([ADR-007](ADR-007-pii-cifrada-e-mascarada.md)).

### A leitura do Raí: a tela "Ler atendimento"

Uma página, um campo de busca por nome ou telefone, e a conversa em ordem cronológica com três
tipos de linha:

```
14:22  Tarcísio   Boa tarde, tenho uns 150 mil, queria um elétrico pra cidade
14:22  Aurora     …
       ⚙ consultou estoque → 4 unidades até R$ 150.000 (14:22:07)
14:23  Aurora     Com 150 mil você tem três opções boas aqui na loja. Antes…
       ⏸ aguardou aprovação da Neuza — aprovado por Neuza Andrade às 14:31
```

As linhas `⚙` e `⏸` são o que transforma transcrição em auditoria: elas mostram **onde o número
veio de fora do modelo** e **onde um humano decidiu**. Sem elas, o Raí lê a conversa e ainda
precisa acreditar em mim.

### O teto: contado em tempo real, cortado em código

| Nível | Valor | O que acontece |
|---|---|---|
| Custo por conversa | R$ 0,45 | Acima disso a conversa entra em revisão; sinaliza loop de agente |
| Alerta mensal | R$ 720 (80%) | WhatsApp para o Raí e para mim |
| Teto mensal | **R$ 900** | Aurora para de atender; toda conversa nova vai direto para fila humana com aviso honesto ao cliente |

O corte é uma checagem antes de cada chamada ao modelo, contra um contador em Redis com o gasto
acumulado do mês. Degradar para atendimento humano é ruim; conta surpresa no dia 30 encerra o
projeto.

## Alternativas consideradas

### Prometheus + Grafana + Loki, como no rascunho inicial — **descartada**

Três serviços que respondem "a API está de pé e o p99 subiu". Nenhum deles responde "por que a
Aurora recomendou o Seal para o Tarcísio", que é a pergunta real do Raí e a minha. Trace de LLM
não é métrica de infra: o dado que importa é hierárquico e textual (prompt, tool call, retorno),
e vira ruído em série temporal.

Para 5 containers numa loja, `docker compose logs` mais healthcheck cobrem a infra. Se a Sol &
Volt crescer, Prometheus volta à mesa — para infra, ainda não para agente.

### Só logar em arquivo e ler com `grep` — **descartada**

Zero dependência, e foi tentador pelo prazo. Descartada porque a fala 4 do Raí é sobre **ele**
abrir e ler, não sobre eu abrir e ler. Um `.log` com JSON por linha atende a mim e a mais
ninguém na Sol & Volt — e "minha equipe consegue colocar pra rodar" é desejo explícito no
enunciado do cliente.

### Observabilidade depois, quando o agente estiver pronto — **descartada, e é a decisão de ordem mais importante deste ADR**

A ordem natural das specs colocaria observabilidade lá no fim. Inverti de propósito: **S-08 é
implementada logo depois do S-02**, antes do agente ([S-03](../spec/S-03-agente-aurora.md)) existir.

O motivo é prático, não estético. Depurar por que um agente escolheu uma tool sem ver o prompt
exato e o retorno daquela chamada é adivinhação — e adivinhação é a atividade mais cara que
existe num projeto de duas semanas. Cada hora gasta instrumentando antes economiza várias horas
de "roda de novo e vê se acontece".

E há um efeito de segunda ordem: instrumentação escrita depois é instrumentação escrita para
confirmar o que eu já acho que o sistema faz.

### Estimar custo por contagem de tokens local — **descartada**

Tokenizer local diverge do faturamento real, e a diferença aparece justo em conversa longa com
tool calls, que é o caso caro. Uso o `usage` que vem na resposta do OpenRouter: é o número que o
Raí vai pagar.

## Consequências

**Aceitas:**

- Mais um container (Langfuse + seu Postgres). É o único serviço de suporte que o v1 carrega, e
  ele existe para atender duas falas diretas do cliente.
- Instrumentar antes do agente atrasa a primeira demo em ~1 dia.
- No teto, o produto degrada para humano. Comportamento correto, e o Raí sabe disso desde o
  contrato.
- A tela "Ler atendimento" é código de produto a manter, não uma ferramenta de terceiro.

**Ganhas:**

- O Raí audita sem depender de mim, que é o que ele pediu.
- Custo deixa de ser fé e vira número por conversa, comparável entre versões do prompt.
- Regressão de qualidade fica visível: dá para comparar duas versões do prompt na mesma conversa
  gravada.
