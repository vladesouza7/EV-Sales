# Arquitetura — EV-Sales

> Não existe arquitetura certa. Existe arquitetura explicada.

O EV-Sales tem **um agente**, **oito serviços que ele pode chamar** e **um ponto onde ele para e
espera uma pessoa**. Tudo o mais é consequência disso.

---

## O desenho

```
                                 INTERNET
                                     │
                        ┌────────────┴────────────┐
                        │                         │
                        ▼                         ▼
                 ┌─────────────┐          ┌──────────────┐
                 │  React+Vite │          │ Evolution API│
                 │  landing    │          │   WhatsApp   │
                 │  chat       │          └──────┬───────┘
                 │  fila Neuza │                 │ webhook
                 │  ler atend. │                 │
                 └──────┬──────┘                 │
                        │ REST + SSE             │
                        └───────────┬────────────┘
                                    ▼
                            ┌───────────────┐
                            │    FastAPI    │
                            │               │
                            │  só enfileira │
                            └───────┬───────┘
                                    ▼
                            ┌───────────────┐
                            │     Redis     │
                            │ fila · lock   │
                            │ contador $$   │
                            └───────┬───────┘
                                    ▼
                    ┌───────────────────────────────┐
                    │           WORKER              │
                    │   ┌───────────────────────┐   │
                    │   │  loop de tool calling │   │
                    │   │      (Aurora)         │   │
                    │   └───────────┬───────────┘   │
                    │               │               │
                    │      tools filtradas          │
                    │        pela etapa             │
                    └───────┬───────────────┬───────┘
                            │               │
                            ▼               ▼
                   ┌────────────────┐  ┌──────────┐
                   │   POSTGRES     │  │OpenRouter│
                   │                │  │   LLM    │
                   │ estoque/chassi │  └──────────┘
                   │ preço · leads  │
                   │ conversas      │
                   │ aprovações     │
                   │ pgvector       │
                   └───────┬────────┘
                           │
                           ▼
                      ┌─────────┐
                      │ Langfuse│  trace · custo
                      └─────────┘

               ┌────────────────────────────────┐
               │  ⏸  A PAUSA                    │
               │                                │
               │  espelho e reserva esperam a   │
               │  Neuza aprovar. O estado é uma │
               │  linha no Postgres — não há    │
               │  processo suspenso.            │
               └────────────────────────────────┘
```

---

## Quantos agentes existem, e por quê

**Um.** A Aurora.

A alternativa natural era separar em dois — um subagente de recomendação, só leitura, e um de
fechamento, com escrita. É um desenho legítimo, e eu o descartei
([ADR-009](adr/ADR-009-sem-framework-de-orquestracao.md)) porque a garantia que ele oferece já vem
de outro lugar, mais barato e mais direto: **as tools são filtradas pela etapa da conversa**.

Na etapa `qualificacao`, não existe tool de escrita na lista enviada ao modelo. Não é uma proibição
no prompt — é uma lista que não contém a função. Na etapa `aguardando_aprovacao`, a lista está
vazia. Um segundo agente daria a mesma garantia, cobrando por isso uma passagem de contexto entre
dois modelos e a perda de nuance da conversa.

A garantia não vem de quantos agentes existem. Vem de qual função está na lista daquele turno.

## O que a Aurora pode e não pode

| Pode | Não pode |
|---|---|
| Ler catálogo, estoque, preço | Escrever preço |
| Buscar conhecimento (pgvector) | Dar desconto — **a função não existe** |
| Registrar a qualificação do lead | Emitir espelho sem `approval_id` |
| Pedir aprovação e **parar** | Reservar sem aprovação válida |
| Reservar chassi, com aprovação | Cancelar reserva |
| Consultar agenda e agendar test drive | Marcar unidade como vendida — só o vendedor marca |
| Transferir para humano | Enviar a primeira mensagem no WhatsApp |
| | Continuar respondendo depois de transferir |

As três da direita em negrito não são regras que o sistema verifica — são **capacidades que não
foram instaladas**. Nenhuma mensagem, por mais criativa, chama uma função que não está registrada
([ADR-004](adr/ADR-004-aprovacao-humana-no-irreversivel.md)).

## Onde ficam os dados

| Dado | Onde | Por quê |
|---|---|---|
| Estoque, chassi, preço | **Postgres** | Fonte da verdade transacional; a reserva é decidida por `UPDATE … WHERE` ([ADR-001](adr/ADR-001-postgres-fonte-da-verdade.md)) |
| Leads, conversas, mensagens | **Postgres** | O Raí precisa reabrir e ler conversa de três semanas atrás |
| Estado da conversa (etapa, qualificação) | **Postgres** | É o estado do agente. Sobrevive a restart, atravessa canais ([ADR-009](adr/ADR-009-sem-framework-de-orquestracao.md)) |
| Aprovações e reservas | **Postgres** | `approval_id NOT NULL` é a garantia, e ela mora no schema |
| Conhecimento semântico | **Postgres + pgvector** | ~2.800 chunks não justificam banco vetorial dedicado — reavaliado com o catálogo real ([ADR-002](adr/ADR-002-pgvector-em-vez-de-qdrant.md)) |
| Fila, lock de conversa, contador de custo | **Redis** | Efêmero por natureza; nada aqui precisa sobreviver |
| Trace, tokens, custo | **Langfuse** | Duas leituras: a do Raí e a minha ([ADR-006](adr/ADR-006-observabilidade-e-teto-de-custo.md)) |
| Fotos e PDFs | **Volume Docker** | ~20 arquivos. Object store é overkill declarado |

**Nada de valor mora só em Redis.** Se o Redis sumir, perdem-se filas em trânsito e o contador do
mês — recuperável do Postgres. Nenhuma conversa, nenhuma reserva, nenhuma aprovação.

## Onde entra o humano

Em três lugares, e só neles:

**1. A Neuza aprova o irreversível.** *"Esse carro sai do estoque para esse cliente, por 72h, com
esse preço escrito."* A conversa para em `aguardando_aprovacao`, uma etapa **sem tools**. Sem
registro de aprovação, não existe caminho de volta ([S-04](spec/S-04-fila-de-aprovacao.md)).

**2. Tarcísio e Jaqueline assumem.** Quando o cliente pede, quando a Aurora erra duas vezes na
verificação numérica, quando o teto de custo é atingido, ou quando a conversa passa de 60 turnos. A
conversa entra em `modo = 'humano'` e **a Aurora silencia** — não sugere, não completa, não
interfere.

**3. O Raí audita.** Tela "Ler atendimento", com as consultas ao estoque e as aprovações visíveis na
linha do tempo ([S-08](spec/S-08-observabilidade-e-custo.md)).

E há um quarto ponto, que é uma **fronteira** e não um passo: depois do test drive, o vendedor marca
o desfecho num toque. A venda em si acontece presencialmente, no sistema que a Sol & Volt já opera —
o EV-Sales só precisa saber se aquele chassi foi vendido, para o catálogo não passar a mentir
([ADR-011](adr/ADR-011-jornada-digital-termina-no-test-drive.md)).

## O que acontece quando algo falha no meio

| Falha | O que acontece | O que **não** acontece |
|---|---|---|
| Container reinicia com conversa parada | Nada. O estado é uma linha no Postgres, não um processo | Perder a conversa |
| LLM fora do ar | Fallback para outro fabricante; se todos falharem, handoff humano | Responder sem consultar |
| Evolution desconecta | Mensagens ficam na fila; alerta imediato | Perder mensagem do cliente — ela é gravada antes de processar |
| Postgres fora do ar | O sistema recusa atender, com mensagem honesta | Atender com estoque adivinhado |
| Redis fora do ar | Turnos processam em série, sem lock; a constraint segura a reserva | Reserva dupla |
| Aurora cita número errado | Regenera uma vez; na segunda, bloqueia e transfere | O texto errado chegar ao cliente |
| Dois clientes, mesmo chassi | Um vence no `UPDATE … WHERE`; o outro recebe alternativa na hora | Prometer o mesmo carro duas vezes |
| Teto de custo atingido | Aurora para; conversas vão para fila humana | Conta surpresa no dia 30 |
| Neuza não responde em 15 min | Escala para o Raí | Emitir sem aprovação |
| Aprovação expira (20 min) | Preço é relido e a aprovação refeita | Emitir com preço vencido |

O padrão: **degradar para humano, nunca para adivinhação.** Em todos os casos, o pior resultado
aceitável é o cliente esperar por uma pessoa. Não é aceitável o cliente receber um número inventado.

## O que foi cortado do desenho original, e por quê

O rascunho inicial ([docs/pesquisa](pesquisa/RASCUNHO-ARQUITETURA-INICIAL.md)) tinha nove serviços.
Cada corte tem ADR:

| Cortado | Substituído por | ADR |
|---|---|---|
| Qdrant | pgvector no mesmo Postgres | [002](adr/ADR-002-pgvector-em-vez-de-qdrant.md) |
| MinIO | Volume Docker | [S-10](spec/S-10-operacao.md) |
| Prometheus + Grafana + Loki | Langfuse + healthcheck | [006](adr/ADR-006-observabilidade-e-teto-de-custo.md) |
| Framework de orquestração | Loop de tools + estado no Postgres | [009](adr/ADR-009-sem-framework-de-orquestracao.md) |
| MongoDB | Postgres | [001](adr/ADR-001-postgres-fonte-da-verdade.md) |

Cada caixa a menos é uma caixa que a equipe da Sol & Volt não precisa entender, e uma que eu
consigo defender. Caixa que não se defende em ADR é passivo, não arquitetura.
