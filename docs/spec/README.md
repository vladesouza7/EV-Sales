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
   S-11 (autenticação) entra antes da S-04 — três entregas pedem "sessão autenticada"
```

A barra dupla é a fronteira do [ADR-011](../adr/ADR-011-jornada-digital-termina-no-test-drive.md):
depois dela, a venda acontece nos sistemas que a Sol & Volt já opera. Só o **desfecho** volta.

**A S-07 saiu da ordem.** Foi implementada em parte logo depois da S-02, a pedido da revisão de
produto, para que a landing tivesse uma porta direta de test drive. A parte que veio é a que não
depende de ninguém: agenda, atribuição de vendedor e gravação. O que depende da S-05 (oferecer o
chassi já reservado para o próprio lead) está no código e fica inerte até lá; o que depende da
S-06 e da S-04 — lembrete, dossiê e desfecho — não veio.

## As specs

Estado em **6 de setembro de 2026**. "Parcial" sempre diz o que falta, no cabeçalho da própria
spec.

A [S-11](S-11-autenticacao-e-perfis.md) nasceu depois das outras dez: três entregas exigiam
"sessão autenticada" e nenhuma dizia o que isso significa. Ambiguidade em spec é decisão
delegada a quem implementa, e autenticação não é decisão para delegar.

| # | Spec | O que entrega | Estado | Portão de CI |
|---|---|---|---|---|
| [S-01](S-01-landing-e-captura-de-lead.md) | Landing e captura de lead | Página, formulário, catálogo somente-leitura | ✔ pronta | |
| [S-02](S-02-chat-web-e-sessao.md) | Chat web e sessão | Streaming, modelo de conversa, etapas | ✔ pronta | |
| [S-03](S-03-agente-aurora.md) | A Aurora | Tools, qualificação, **verificação numérica** | ◐ parcial — agente, tools e verificação sim; evals e 2 tools não | ✅ preço · ✅ autonomia · ✅ injection |
| [S-04](S-04-fila-de-aprovacao.md) | Fila de aprovação | A pausa, a tela da Neuza, o Espelho de Condição | ◐ parcial — backend e Espelho sim; a tela não | |
| [S-05](S-05-reserva-de-chassi.md) | Reserva de chassi | A operação que não pode falhar | ✗ não começada | ✅ concorrência |
| [S-06](S-06-handoff-whatsapp.md) | Handoff WhatsApp | Token, webhook, continuidade | ✗ não começada | |
| [S-07](S-07-test-drive.md) | Test drive | Agenda real, dossiê do vendedor, **desfecho** | ◐ parcial — agenda sim; §6, §8 e §9 não | |
| [S-08](S-08-observabilidade-e-custo.md) | Trace e custo | "Ler atendimento", teto que corta | ◐ parcial — trilha, custo e teto sim; as duas telas não | |
| [S-09](S-09-protecao-de-pii.md) | Proteção de PII | Cifragem, mascaramento, retenção | ◐ parcial — cifragem e máscara sim; retenção não | ✅ varredura de logs |
| [S-10](S-10-operacao.md) | Operação | Compose, seed, backup, runbook, CI | ◐ parcial — compose e seed sim; backup, runbook e CI não | |
| [S-11](S-11-autenticacao-e-perfis.md) | Autenticação e perfis | Login, sessão, o que cada perfil alcança | ◐ parcial — login, sessão e perfis sim; as telas são da S-04 e da S-08 | |

Os cinco ✅ são os portões que **reprovam o build**. Eles existem porque risco sem verificação
automatizada é desejo, não requisito: enquanto o eval não bloqueia o merge, o ADR envelhece em
silêncio dizendo que está tudo mitigado.

**Nenhum dos cinco existe hoje**, e é bom que isso esteja escrito em vez de subentendido: três
dependem da S-03, um da S-05, e a varredura de PII existe como teste pontual por endpoint
([S-09](S-09-protecao-de-pii.md)), não como varredura. O CI que os executa é entrega da
[S-10](S-10-operacao.md) e ainda não foi escrito.

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
