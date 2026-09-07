# S-07 — Agendamento de test drive

**Depende de:** [S-05](S-05-reserva-de-chassi.md)
**Decide por:** [ADR-001](../adr/ADR-001-postgres-fonte-da-verdade.md)
**Estado:** ◐ parcial — ver o quadro abaixo

---

## O que já está implementado

Esta spec foi implementada **em parte e fora de ordem** (ver
[README das specs](README.md#ordem-de-implementação)), para que a landing tivesse uma porta direta
de test drive antes da Aurora existir.

| § | O quê | Estado |
|---|---|---|
| §1 | `vendedores`, `agenda_bloqueios`, `test_drives` e os dois `EXCLUDE` | ✔ |
| §2 | Janela de atendimento, duração, intervalo, almoço, antecedências | ✔ |
| §3 | Oferta de no máximo 3 horários reais | ✔ pela página `/test-drive`; **não** como tool da Aurora |
| §4 | Gravação, atribuição de vendedor, conversa em `encerrada` com desfecho | ✔ exceto a notificação ao vendedor (§4.3), que depende da [S-06](S-06-handoff-whatsapp.md) |
| §5 | Confirmação para o cliente | ✔ na tela; ainda não pelo WhatsApp |
| §6 | Lembrete de 24h | ✗ — a [S-06](S-06-handoff-whatsapp.md) destravou o envio, mas o lembrete não foi escrito |
| §7 | Remarcação e cancelamento | ✗ |
| §8 | Dossiê do atendimento | ✗ as objeções vêm da `buscar_conhecimento`, tool que a [S-03](S-03-agente-aurora.md) não implementou |
| §9 | Registro de desfecho | ✔ `/desfecho`, `app/testdrive.py` — os quatro desfechos, o rastro e as duas cobranças |

Duas consequências de ter vindo antes da S-05, e as duas são deliberadas:

1. A condição §3.4 — *"o chassi está `reservado` para este lead ou `disponivel`"* — está no código
   e o ramo do `reservado` deixou de ser inerte quando a [S-05](S-05-reserva-de-chassi.md) entrou.
2. O §9 é hoje o **único** caminho para `unidades.status = 'vendido'`, e é por toque de gente
   autenticada: a rotina cobra o desfecho duas vezes e nunca o preenche. O CHECK
   `ck_test_drives_desfecho_tem_autor` recusa desfecho sem quem marcou e sem quando — a spec diz
   "o sistema nunca marca vendido sozinho", e isso virou constraint, não intenção.

---

## Objetivo

Fechar a jornada digital: marcar o test drive contra a **agenda real** de Tarcísio e Jaqueline, sem
oferecer horário que não existe e sem marcar dois clientes no mesmo carro na mesma hora — entregar
ao vendedor um **dossiê** do que já foi conversado, e receber de volta o **desfecho**.

> O test drive é o fim do escopo do EV-Sales
> ([ADR-011](../adr/ADR-011-jornada-digital-termina-no-test-drive.md)). A venda é fechada
> presencialmente, no sistema que a Sol & Volt já opera. O desfecho (§8) é a única informação que
> atravessa essa fronteira de volta — e sem ela o catálogo passa a mentir.

## Comportamento

### 1. Modelo de dados

```
vendedores
  id, nome, telefone_cifrado, ativo

agenda_bloqueios              -- férias, folga, compromisso pessoal
  id, vendedor_id, inicio, fim, motivo

test_drives
  id, conversa_id, lead_id, chassi, vendedor_id,
  inicio, fim,
  status ('agendado' | 'confirmado' | 'realizado' | 'nao_compareceu' | 'cancelado'),
  criado_em, confirmado_em
```

Restrições no banco, não na aplicação:

- Índice de exclusão impede sobreposição de intervalo **por vendedor**.
- Índice de exclusão impede sobreposição de intervalo **por chassi** — o mesmo carro não sai duas
  vezes no mesmo horário.

### 2. Janela de atendimento

| | |
|---|---|
| Segunda a sexta | 09:00 – 18:00 |
| Sábado | 09:00 – 13:00 |
| Domingo e feriado | fechado |
| Duração do test drive | 45 min |
| Intervalo entre agendamentos | 15 min (volta, conferência do carro) |
| Antecedência mínima | 2 horas |
| Antecedência máxima | 14 dias |
| Almoço | 12:00 – 13:00, bloqueado |

Fuso: `America/Fortaleza` (a Paraíba não tem horário de verão; fixar o fuso evita o bug clássico).

### 3. `consultar_agenda`

Recebe uma preferência em linguagem natural já interpretada (`manha` / `tarde` / data específica) e
devolve **no máximo 3 horários**, o primeiro sendo o mais próximo disponível.

Três opções, não uma lista: lista longa gera indecisão, e uma só parece imposição.

O horário só é oferecido se, cumulativamente:

1. Está na janela de atendimento;
2. O vendedor não tem bloqueio nem outro test drive;
3. O **chassi** não tem outro test drive;
4. O chassi está `reservado` para este lead ou `disponivel`.

### 4. `agendar_test_drive`

Em uma transação:

1. Reconfere as quatro condições.
2. `INSERT` — se o índice de exclusão recusar, devolve `horario_indisponivel` e a Aurora oferece as
   próximas 3 opções.
3. Notifica o vendedor pelo WhatsApp.
4. Conversa vai para `encerrada` com `desfecho = 'test_drive_agendado'`.

**Atribuição do vendedor:** quem tiver menos test drives agendados nos próximos 7 dias; empate
resolve por ordem alfabética. Simples de propósito — rodízio ponderado por conversão é otimização
sem dado no v1.

### 5. Confirmação para o cliente

```
✅ Test drive agendado!

📅 Quinta, 5 de setembro, às 14h
🚗 BYD Seal Premium · branco
📍 Sol & Volt — Av. Epitácio Pessoa, 2.140, Tambaú
👤 Com o Tarcísio

Leve sua CNH. Se precisar remarcar, é só me chamar aqui.
```

A CNH é conferida **presencialmente**, no balcão. O sistema não coleta, não fotografa e não guarda
documento ([ADR-007](../adr/ADR-007-pii-cifrada-e-mascarada.md)).

### 6. Lembrete

Único, **24h antes** (ou às 18h do dia anterior, se o agendamento for para a manhã seguinte):

> "Oi, Tarcísio! Passando pra lembrar do seu test drive amanhã às 14h, com o Tarcísio aqui na loja.
> Confirma pra mim? Se precisar remarcar, é só falar."

Este é o **único envio automático do v1**, e ele é legítimo: acontece dentro de uma conversa que o
cliente iniciou, sobre um compromisso que ele marcou ([ADR-005](../adr/ADR-005-handoff-whatsapp-por-wa-me.md)).
Fora da janela de 24h do WhatsApp, o lembrete vira tarefa para o vendedor ligar.

Resposta afirmativa → `confirmado`. Sem resposta → segue `agendado`, e o vendedor vê isso na tela.

### 7. Remarcação e cancelamento

Remarcar é cancelar e agendar de novo, na mesma transação. Máximo de **2 remarcações** pela Aurora;
a terceira vai para humano.

Cancelar o test drive **não libera a reserva do chassi** — são coisas diferentes, e liberar carro é
decisão comercial ([S-05](S-05-reserva-de-chassi.md)).

### 8. Dossiê do atendimento

Enviado ao vendedor no WhatsApp **2 horas antes** do test drive, com link para a versão completa.
Hoje o Tarcísio recebe "lead quente" e começa a conversa do zero, repetindo perguntas que o cliente
já respondeu.

```
Tarcísio Nóbrega · quinta, 14h · BYD Seal branco …4471 · R$ 249.990

Rotina    40 km/dia · Manaíra→Centro · carregador em casa (tomada comum)
Orçamento até R$ 260 mil · não falou em financiamento
Perfil    primeiro elétrico · nunca dirigiu um

⚠ Objeções levantadas
  · medo de ficar sem bateria na estrada — a Aurora respondeu com a rotina dele
  · perguntou duas vezes sobre vida útil da bateria

💡 Não perguntou sobre financiamento. Vale abrir o assunto.

Conversa completa →
```

| Regra | Valor |
|---|---|
| Origem dos campos | `conversas.qualificacao` e as tools chamadas — **nada gerado livremente** |
| Objeções | extraídas das chamadas de `buscar_conhecimento` no histórico |
| Sugestões (`💡`) | regra determinística sobre campos vazios, não texto do modelo |
| Preço | relido do Postgres na hora de montar |
| PII | nome completo liberado — o vendedor tem acesso ao lead dele ([S-09](S-09-protecao-de-pii.md)) |

O dossiê é um **resumo de dados estruturados**, não uma redação. Um modelo escrevendo livremente
sobre o cliente reintroduziria alucinação exatamente onde o vendedor mais confia
([ADR-003](../adr/ADR-003-numeros-nunca-saem-do-modelo.md)).

### 9. Registro de desfecho

Tela de um toque, no celular do vendedor, disparada **2 horas depois** do horário do test drive:

```
   Tarcísio Nóbrega · BYD Seal …4471 · quinta 14h

   compareceu?     [ sim ]     [ não ]
        │
        ▼
   e aí?    [ vendeu ]   [ vai pensar ]   [ desistiu ]
```

| Desfecho | `unidades.status` | Reserva | Lead |
|---|---|---|---|
| `vendeu` | **`vendido`** | encerrada | ganho |
| `vai pensar` | `reservado` | mantida até as 72h | em negociação |
| `desistiu` | **`disponivel`** | liberada | perdido |
| `nao_compareceu` | `reservado` | mantida até as 72h | a recontatar |

`vendeu` é o único ponto em que o EV-Sales sabe que a venda aconteceu. **Não há integração com o
DMS** — o desfecho custa um toque, 11 vezes por mês, e automatizar isso ao custo de uma integração
é complexidade sem retorno ([ADR-011](../adr/ADR-011-jornada-digital-termina-no-test-drive.md)).

**Contra o esquecimento**, que é o risco real desta tela:

| Quando | O quê |
|---|---|
| +2h do test drive | Notificação ao vendedor |
| +24h sem desfecho | Segunda notificação, e o item entra na tela da Neuza |
| Reserva vence sem desfecho | Alerta `reserva_orfa` ([S-08](S-08-observabilidade-e-custo.md)) — catálogo mentindo é falha grave |

O sistema **nunca** marca `vendido` sozinho, por inferência ou por tempo. Só por toque de vendedor
autenticado, com auditoria.

---

## Critérios de aceite

```gherkin
Cenário: só horários reais são oferecidos
  Dado que Tarcísio tem test drive às 14h e Jaqueline está de folga
  Quando a Aurora consulta a agenda para quinta à tarde
  Então nenhuma das opções é às 14h com Tarcísio
  E nenhuma opção é com Jaqueline

Cenário: no máximo 3 opções
  Quando a Aurora consulta a agenda
  Então recebe no máximo 3 horários
  E o primeiro é o mais próximo disponível

Cenário: o mesmo carro não sai duas vezes na mesma hora
  Dado um test drive do chassi "…4471" quinta às 14h com Tarcísio
  Quando tento agendar o mesmo chassi quinta às 14h com Jaqueline
  Então o banco recusa por sobreposição
  E a Aurora oferece outros horários

Cenário: fora da janela de atendimento
  Quando o cliente pede domingo às 10h
  Então nenhuma opção de domingo é oferecida
  E a Aurora explica o horário da loja

Cenário: antecedência mínima
  Dado que agora são 13h de quinta
  Quando o cliente pede "hoje às 14h"
  Então esse horário não é oferecido
  E a primeira opção é 15h ou depois

Cenário: distribuição entre vendedores
  Dado que Tarcísio tem 4 agendamentos e Jaqueline tem 1 nos próximos 7 dias
  Quando um novo test drive é agendado
  Então ele vai para Jaqueline

Cenário: lembrete respeita a janela do WhatsApp
  Dado um test drive amanhã às 14h
  E que a última mensagem do cliente foi há 30 horas
  Então o lembrete automático não é enviado
  E uma tarefa de ligação é criada para o vendedor

Cenário: cancelar test drive não libera o carro
  Quando o test drive é cancelado
  Então o status da unidade continua "reservado"

Cenário: o dossiê não inventa nada
  Quando o dossiê é montado
  Então cada campo corresponde a um valor de conversas.qualificacao ou a um retorno de tool
  E o preço foi relido do Postgres
  E nenhum texto livre gerado pelo modelo aparece no documento

Cenário: vendeu marca a unidade e encerra a reserva
  Dado um test drive realizado do chassi "…4471"
  Quando o vendedor marca "vendeu"
  Então unidades.status vira "vendido"
  E a reserva é encerrada
  E o lead é encerrado como ganho

Cenário: desistiu devolve o carro ao estoque
  Quando o vendedor marca "desistiu"
  Então unidades.status volta a "disponivel"
  E o chassi volta a aparecer em buscar_unidades

Cenário: o sistema nunca marca vendido sozinho
  Dado um test drive realizado há 30 dias sem desfecho registrado
  Quando qualquer rotina automática roda
  Então unidades.status não é "vendido"

Cenário: reserva vencida sem desfecho gera alerta
  Dado uma reserva que venceu sem desfecho registrado
  Então um alerta "reserva_orfa" é emitido
  E o item aparece na tela da Neuza

Cenário: desfecho exige vendedor autenticado
  Quando o desfecho é registrado
  Então a auditoria grava quem marcou, quando e de qual IP
```

## Fora do escopo

- **O fechamento da venda**: negociação final, financiamento, faturamento, NF-e e documentação
  acontecem presencialmente ([ADR-011](../adr/ADR-011-jornada-digital-termina-no-test-drive.md)).
- Integração com o DMS da concessionária — o desfecho é manual, por decisão.
- Integração com Google Calendar ou Outlook.
- Coleta ou upload de CNH — conferida no balcão.
- Test drive em domicílio.
- Fila de espera por horário.
- Pesquisa de satisfação pós-test drive.
