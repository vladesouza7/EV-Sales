# SPECs — EV-Sales

Especificações de implementação. Cada uma descreve o comportamento esperado sem ambiguidade, de
forma que outra pessoa — ou um agente de código — consiga implementar sem adivinhar, e traz
critérios de aceite em Gherkin que viram teste.

## Ordem de implementação

A ordem **não** é numérica. A [S-08](S-08-observabilidade-e-custo.md) vem antes da
[S-03](S-03-agente-aurora.md) por decisão explícita do [ADR-006](../adr/ADR-006-observabilidade-e-teto-de-custo.md):
depurar agente sem trace é adivinhação, e adivinhação é a atividade mais cara de um projeto curto.

```
S-01 ──▶ S-02 ──▶ S-08 ──▶ S-03 ──▶ S-04 ──▶ S-05 ──▶ S-07 ──▶ ‖ venda presencial
landing   chat    trace    Aurora   aprovação reserva  test drive       (fora do escopo)
                    │        │                                  │
                    │        └──▶ S-06  handoff WhatsApp     desfecho ◀─ 1 toque
                    │
   S-09 (PII) e S-10 (operação) atravessam todas, desde a primeira linha
```

A barra dupla é a fronteira do [ADR-011](../adr/ADR-011-jornada-digital-termina-no-test-drive.md):
depois dela, a venda acontece nos sistemas que a Sol & Volt já opera. Só o **desfecho** volta.

## As specs

| # | Spec | O que entrega | Portão de CI |
|---|---|---|---|
| [S-01](S-01-landing-e-captura-de-lead.md) | Landing e captura de lead | Página, formulário, catálogo somente-leitura | |
| [S-02](S-02-chat-web-e-sessao.md) | Chat web e sessão | Streaming, modelo de conversa, etapas | |
| [S-03](S-03-agente-aurora.md) | A Aurora | Tools, qualificação, **verificação numérica** | ✅ preço · ✅ autonomia · ✅ injection |
| [S-04](S-04-fila-de-aprovacao.md) | Fila de aprovação | A pausa, a tela da Neuza, o Espelho de Condição | |
| [S-05](S-05-reserva-de-chassi.md) | Reserva de chassi | A operação que não pode falhar | ✅ concorrência |
| [S-06](S-06-handoff-whatsapp.md) | Handoff WhatsApp | Token, webhook, continuidade | |
| [S-07](S-07-test-drive.md) | Test drive | Agenda real, dossiê do vendedor, **desfecho** | |
| [S-08](S-08-observabilidade-e-custo.md) | Trace e custo | "Ler atendimento", teto que corta | |
| [S-09](S-09-protecao-de-pii.md) | Proteção de PII | Cifragem, mascaramento, retenção | ✅ varredura de logs |
| [S-10](S-10-operacao.md) | Operação | Compose, seed, backup, runbook, CI | |

Os cinco ✅ são os portões que **reprovam o build**. Eles existem porque risco sem verificação
automatizada é desejo, não requisito: enquanto o eval não bloqueia o merge, o ADR envelhece em
silêncio dizendo que está tudo mitigado.

## Como uma spec vira código neste repositório

1. A spec é escrita e revisada **antes** da implementação.
2. Os cenários Gherkin viram testes — os testes primeiro, falhando.
3. A implementação é feita com agente de código, com a spec no contexto.
4. `/verificar-spec` roda numa **sessão limpa**, que nunca viu a implementação: lê a spec, lê o
   código, e emite veredito. Essa sessão **não tem permissão de corrigir** o que encontrar —
   revisor que conserta virou autor ([CLAUDE.md](../../CLAUDE.md)).
5. O PR só abre com CI verde e o veredito anexado.

## Formato

**Objetivo** (uma frase) → **Comportamento** (numerado, com tabelas de valores concretos e sem
"deveria") → **Critérios de aceite** (Gherkin executável) → **Fora do escopo** (com o motivo, ou
com link para o ADR que decidiu).

Valores são absolutos, nunca relativos: "20 minutos", não "um tempo razoável"; "no máximo 3
opções", não "algumas opções". Ambiguidade em spec é decisão delegada a quem implementa — e quem
implementa aqui é frequentemente um agente.
