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
   S-12 (configurações) entra antes da S-06 — é lá que o número e a chave passam a existir
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

A [S-12](S-12-configuracoes.md) nasceu a pedido do analista, e muda uma decisão: o
[ADR-012](../adr/ADR-012-provedor-configuravel.md) tinha posto a configuração do provedor no
`.env`, e a [ADR-014](../adr/ADR-014-configuracao-operacional-no-banco.md) a tira de lá. Ela entra
**antes** da [S-06](S-06-handoff-whatsapp.md) porque é onde o número da loja e a chave da Evolution
passam a existir.

| # | Spec | O que entrega | Estado | Portão de CI |
|---|---|---|---|---|
| [S-01](S-01-landing-e-captura-de-lead.md) | Landing e captura de lead | Página, formulário, catálogo somente-leitura | ✔ pronta | |
| [S-02](S-02-chat-web-e-sessao.md) | Chat web e sessão | Streaming, modelo de conversa, etapas | ✔ pronta | |
| [S-03](S-03-agente-aurora.md) | A Aurora | Tools, qualificação, **verificação numérica** | ◐ parcial — agente, tools e verificação sim; evals e 2 tools não | ✅ preço · ✅ autonomia · ✅ injection |
| [S-04](S-04-fila-de-aprovacao.md) | Fila de aprovação | A pausa, a tela da Neuza, o Espelho de Condição | ◐ parcial — fila, Espelho e tela sim; a notificação por WhatsApp não | |
| [S-05](S-05-reserva-de-chassi.md) | Reserva de chassi | A operação que não pode falhar | ◐ parcial — a operação e o portão sim; a rotina periódica não | ✅ concorrência — **existe** |
| [S-06](S-06-handoff-whatsapp.md) | Handoff WhatsApp | Token, webhook, continuidade | ✗ não começada | |
| [S-07](S-07-test-drive.md) | Test drive | Agenda real, dossiê do vendedor, **desfecho** | ◐ parcial — agenda sim; §6, §8 e §9 não | |
| [S-08](S-08-observabilidade-e-custo.md) | Trace e custo | "Ler atendimento", teto que corta | ◐ parcial — trilha, custo, teto e as duas telas sim; Langfuse não | |
| [S-09](S-09-protecao-de-pii.md) | Proteção de PII | Cifragem, mascaramento, retenção | ◐ parcial — cifragem, máscara e a varredura sim; retenção não | ✅ varredura de logs — **existe** |
| [S-10](S-10-operacao.md) | Operação | Compose, seed, backup, runbook, CI | ◐ parcial — compose, seed e o CI sim; backup e runbook não | |
| [S-11](S-11-autenticacao-e-perfis.md) | Autenticação e perfis | Login, sessão, o que cada perfil alcança | ◐ parcial — login, sessão e perfis sim; as telas são da S-04 e da S-08 | |
| [S-12](S-12-configuracoes.md) | Configurações | Credencial e número sem `ssh`, cifrados | ◐ parcial — tela, rotas e leitor sim; o estado da instância na tela não | |

Os cinco ✅ são os portões que **reprovam o build**. Eles existem porque risco sem verificação
automatizada é desejo, não requisito: enquanto o eval não bloqueia o merge, o ADR envelhece em
silêncio dizendo que está tudo mitigado.

**Dois dos cinco existem hoje**, e os dois foram aceitos do mesmo jeito — rodando contra um erro
de propósito, porque portão que não reprova o código errado é decoração:

- o teste de concorrência da [S-05](S-05-reserva-de-chassi.md), verificado contra uma versão
  deliberadamente quebrada da operação;
- a varredura de PII da [S-09 §7](S-09-protecao-de-pii.md), que roda um atendimento com telefone e
  sobrenome sintéticos e procura os dois no log e na trilha — e cujo primeiro teste é um vazamento
  proposital, para provar que ela pega.

Os outros três dependem dos evals da [S-03](S-03-agente-aurora.md). O CI que executa os portões é
entrega da [S-10 §7](S-10-operacao.md) e está em `.github/workflows/ci.yml`: `main` protegida, PR
obrigatório, e um portão vermelho bloqueia o merge de verdade.

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
