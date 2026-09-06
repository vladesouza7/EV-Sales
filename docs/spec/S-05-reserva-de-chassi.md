# S-05 — Reserva de chassi

**Depende de:** [S-04](S-04-fila-de-aprovacao.md)
**Decide por:** [ADR-001](../adr/ADR-001-postgres-fonte-da-verdade.md)
**Estado:** ◐ parcial — a operação, as pré-condições, a liberação e o **portão de concorrência**
sim; a rotina periódica e o aviso ao vendedor não.

| § | O quê | Estado |
|---|---|---|
| §1 | `UPDATE … WHERE status='disponivel'`, tudo numa transação | ✔ `app/reserva.py` |
| §2 | As cinco pré-condições | ✔ e mais uma: o `approval_id` tem de ser **desta** conversa |
| §3 | Quem perde volta para `recomendacao` sem o chassi | ✔ |
| §4 | 72h, liberação, renovação (máx. 2) | ◐ `liberar_vencidas` existe; **quem a chama a cada 5 min é a [S-10](S-10-operacao.md)**, e o aviso ao vendedor é da [S-06](S-06-handoff-whatsapp.md) |
| §5 | Concorrência entre canais | ✔ o lock da S-02 e o UPDATE são independentes, como a spec pede |
| §6 | **Portão de CI: 50 simultâneas, 1 vencedor** | ✔ `tests/test_reserva.py` |

**Uma pré-condição a mais do que a §2 lista**, e ela é de segurança: o `approval_id` chega pelo
modelo, e o modelo lê texto do cliente. A tool confere que aquele pedido pertence **a esta
conversa** — sem isso, um id de aprovação de outra conversa reservaria o carro para o cliente
errado.

---

## Objetivo

Tirar uma unidade do estoque para um cliente, de forma que **duas pessoas nunca reservem o mesmo
chassi** — nem quando clicam no mesmo milissegundo, uma pelo chat web e outra pelo WhatsApp.

É o medo que o Raí repetiu mais vezes ([CASE — fala 2](../CASE.md#o-que-ele-disse-com-as-palavras-dele)).

## Comportamento

### 1. A operação

Uma instrução condicional, dentro de uma transação:

```sql
UPDATE unidades
   SET status         = 'reservado',
       reservado_para = :lead_id,
       reservado_em   = now()
 WHERE chassi = :chassi
   AND status = 'disponivel'      -- ← a corrida é decidida aqui, pelo Postgres
RETURNING chassi, preco_centavos;
```

**Zero linhas = perdeu a corrida.** Não há `SELECT` antes para "verificar se está disponível":
verificar e depois gravar cria a janela em que o Raí precisa dar o telefonema.

Na mesma transação:

1. `INSERT` em `reservas` com o `approval_id` e o `espelho_id`.
2. Conversa vai para `test_drive`.
3. Registra evento de auditoria.

Falhou qualquer passo, tudo desfaz — a unidade volta a `disponivel` porque nunca chegou a sair.

### 2. Pré-condições, verificadas na transação

| Condição | Se falhar |
|---|---|
| `unidades.status = 'disponivel'` | `indisponivel` |
| Existe `espelho` para esta conversa e este chassi | `sem_espelho` — erro interno, registra incidente |
| `pedidos_de_aprovacao.status = 'aprovado'` e não expirado | `aprovacao_invalida` |
| Chassi da reserva == chassi do espelho | `chassi_divergente` — incidente grave |
| O lead não tem outra reserva ativa | `ja_tem_reserva` |

`reservar_chassi` exige `approval_id` como argumento obrigatório, e ele é validado contra o banco.
A tool **não confia** no que o modelo passou.

### 3. Quando perde a corrida

Acontece de verdade: dois clientes conversando com a Aurora sobre o único Seal branco. O segundo
recebe honestidade imediata, e uma alternativa concreta:

> "Poxa, Tarcísio — esse Seal branco acabou de ser reservado por outro cliente, agora há pouco. Me
> desculpa. Eu tenho o mesmo Seal na cor cinza, mesmo preço, disponível agora. Quer ver?"

A Aurora volta para `recomendacao`, com o chassi indisponível excluído. **Não fica na fila
esperando liberar** — reserva expirada libera o carro, mas o cliente não espera por isso.

O `pedido_de_aprovacao` correspondente é marcado `expirado` (foi aprovado para um chassi que não
existe mais como disponível).

### 4. Validade e liberação

| | |
|---|---|
| Validade da reserva | **72 horas corridas** |
| Libera automaticamente | sim, rotina a cada 5 min |
| Ao liberar | volta a `disponivel`, notifica o vendedor responsável |
| Renovação | só por vendedor autenticado, +72h, no máximo 2 vezes |
| Cancelamento pelo cliente | pede handoff para humano; a Aurora não cancela reserva |

**O que encerra a reserva** ([ADR-011](../adr/ADR-011-jornada-digital-termina-no-test-drive.md)):
o desfecho marcado pelo vendedor depois do test drive ([S-07](S-07-test-drive.md)). `vendeu` move a
unidade para `vendido`; `desistiu` libera de volta para `disponivel`; `vai pensar` mantém a reserva
até o prazo. Reserva que vence **sem desfecho registrado** gera alerta — reserva órfã é catálogo
mentindo, e catálogo mentindo destrói a premissa do [ADR-001](../adr/ADR-001-postgres-fonte-da-verdade.md).

A Aurora **não tem tool de cancelamento**. Liberar um carro é decisão comercial: pode haver sinal
combinado, test drive marcado, cliente a caminho da loja.

### 5. Concorrência entre canais

O mesmo lead pode ter a janela do chat aberta e o WhatsApp ativo. O lock por `conversa_id`
([S-02 §4](S-02-chat-web-e-sessao.md#4-envio-de-mensagem)) impede dois turnos simultâneos na mesma
conversa; o `UPDATE … WHERE` impede o resto. Os dois mecanismos são independentes de propósito: o
lock é otimização, a constraint é garantia.

### 6. Teste de concorrência — obrigatório no CI

```python
# 50 tentativas simultâneas de reservar o mesmo chassi
# exatamente 1 sucesso, 49 "indisponivel", 0 exceções não tratadas
```

Roda contra um Postgres real (não mock, não SQLite). Falhou, o build reprova
([PRD §6.2](../PRD.md#62-segurança-e-custo--critérios-de-bloqueio)).

---

## Critérios de aceite

```gherkin
Cenário: reserva bem-sucedida
  Dado o chassi "…4471" disponível e um espelho aprovado
  Quando a Aurora chama "reservar_chassi"
  Então o status vira "reservado" para o lead
  E uma linha em "reservas" é criada com o approval_id
  E a conversa vai para "test_drive"

Cenário: 50 reservas simultâneas, exatamente 1 vence
  Dado o chassi "…4471" disponível
  Quando 50 requisições tentam reservá-lo ao mesmo tempo
  Então exatamente 1 retorna sucesso
  E 49 retornam "indisponivel"
  E o chassi tem exatamente 1 reserva ativa

Cenário: quem perde recebe alternativa, não erro
  Dado que o chassi acabou de ser reservado por outro cliente
  Quando a Aurora tenta reservar
  Então o cliente recebe o aviso e uma alternativa do mesmo modelo
  E a conversa volta para "recomendacao"
  E o chassi indisponível não aparece na nova busca

Cenário: sem aprovação válida, não reserva
  Dado um approval_id expirado
  Quando a Aurora chama "reservar_chassi"
  Então retorna "aprovacao_invalida"
  E o status da unidade não muda

Cenário: chassi divergente do espelho é incidente
  Dado um espelho para "…4471"
  Quando a tool é chamada com "…9902"
  Então retorna "chassi_divergente"
  E um incidente grave é registrado no trace
  E nenhuma unidade é reservada

Cenário: reserva expira em 72h
  Dado uma reserva criada há 73 horas
  Quando a rotina de liberação roda
  Então a unidade volta a "disponivel"
  E o vendedor responsável é notificado

Cenário: a Aurora não cancela reserva
  Quando o cliente pede para cancelar
  Então nenhuma tool de cancelamento existe na lista do turno
  E a conversa é transferida para humano
```

## Fora do escopo

- Sinal ou pagamento antes do test drive ([ADR-011](../adr/ADR-011-jornada-digital-termina-no-test-drive.md)).
- Lista de espera para unidade reservada.
- Reserva de veículo em trânsito ou não faturado.
- Reserva de mais de uma unidade pelo mesmo lead.
