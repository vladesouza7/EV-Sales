# ADR-004 — Aprovação humana antes do irreversível, e desconto que não existe

**Status:** aceito
**Data:** 2026-09-03
**Decide sobre:** [CASE.md — fala 3 do Raí](../CASE.md#o-que-ele-disse-com-as-palavras-dele)
**Refinado por:** [ADR-011](ADR-011-jornada-digital-termina-no-test-drive.md) — o **objeto** da aprovação mudou; o mecanismo, não

---

## Contexto

> "Proposta com preço escrito não sai sem a Neuza ver. E desconto quem dá sou eu — não é o robô,
> não é o vendedor, não é ninguém."

Duas exigências diferentes na mesma frase, e elas pedem mecanismos diferentes.

A primeira é sobre **momento**: existe um ponto na jornada depois do qual não há volta. Na Sol &
Volt esse ponto é duplo e quase simultâneo — sai um documento com preço escrito (o cliente guarda
e vai cobrar por ele), e o chassi sai do estoque (o Raí liga desmarcando alguém).

> **Precisão de escopo, por [ADR-011](ADR-011-jornada-digital-termina-no-test-drive.md):** o
> documento não é contrato de venda, é o **Espelho de Condição e Reserva** que sustenta o test
> drive. A venda em si — financiamento, faturamento, NF-e — acontece presencialmente, no sistema
> que a Sol & Volt já opera, e tem uma segunda aprovação que não é do EV-Sales.
>
> Isso **não** reduz o irreversível daqui: o chassi reservado continua saindo do estoque para todos
> os outros clientes, e continua sendo o telefonema que o Raí não quer dar. A Neuza deixa de aprovar
> "emissão de documento de venda" e passa a aprovar *"esse carro sai do estoque para esse cliente,
> por 72h, com esse preço escrito"*. Tudo o que vem abaixo neste ADR vale igual.

A segunda é sobre **poder**: desconto é a alavanca comercial do Raí, e ele não quer que ela
esteja ao alcance de ninguém — nem de um modelo de linguagem, nem de um vendedor bem
intencionado, nem de alguém que escreva uma mensagem esperta no chat.

## Decisão

### Parte 1 — O grafo pausa, com o estado no Postgres

Proposta e reserva ficam atrás de uma **interrupção com estado persistido**. Quando a Aurora
chega ao ponto de emitir, ela não emite: ela grava um `pedido_de_aprovacao` no Postgres, com o
chassi, o preço lido do banco, o resumo da qualificação e o `trace_id` da conversa, e o fluxo
para. Para o cliente, ela diz que está confirmando a condição com a gerência — que é a verdade.

A retomada só existe a partir de um registro de decisão da Neuza. Não há caminho de código que
emita proposta sem um `approval_id` válido: a coluna é `NOT NULL` com foreign key, e o teste do
[S-04](../spec/S-04-fila-de-aprovacao.md) tenta emitir sem aprovação e exige que o banco recuse.

> Sem registro de aprovação, não existe caminho de volta. A pausa é primitivo de arquitetura,
> não tela.

Aprovação expira em **20 minutos** — preço com câmbio volátil não pode ser aprovado às 9h e
emitido às 17h. Expirada, o valor é relido do banco e a aprovação refeita.

### Parte 2 — Desconto não é proibido; ele não existe

A Aurora **não tem uma tool de desconto**. Não há função registrada que altere preço, e o campo
`preco_centavos` do espelho é preenchido pelo servidor a partir do Postgres, sem passar
pelo modelo em momento algum.

A diferença entre "o prompt proíbe desconto" e "não existe função de desconto" é a diferença
entre uma regra que pode ser argumentada e uma capacidade que não está instalada. Nenhuma
mensagem — por mais criativa que seja — chama uma função que não foi registrada. Quando o
cliente pede desconto, a Aurora diz que condição especial é com a Neuza, e oferece o handoff.

Desconto continua existindo no negócio: a Neuza o aplica na tela dela, autenticada, e a alteração
entra no log de auditoria com autor e horário.

## Alternativas consideradas

### Aurora emite e a Neuza revisa depois — **descartada**

Descartada pela definição de irreversível. O PDF já está no WhatsApp do cliente; o chassi já saiu
do estoque. Revisar depois é o telefonema que o Raí não quer dar. "Aprovação" que acontece depois
do fato é auditoria, e ele pediu as duas coisas.

### Aprovação só acima de um valor (ex.: > R$ 250 mil) — **descartada**

Sedutora, porque reduziria a fila da Neuza em talvez 60%. Recusada por dois motivos. O primeiro é
que o Dolphin Mini de R$ 118.900 também é uma unidade única, e prometê-lo duas vezes gera o mesmo
telefonema. O segundo é que qualquer limiar vira alvo: bastaria a conversa levar o valor para
baixo da linha para escapar do controle.

Se a fila virar gargalo real na operação, o caminho é **aprovação mais rápida**, não menos
aprovação.

### Guardrail de LLM julgando se a proposta pode sair — **descartada**

Um segundo modelo verificando o primeiro é probabilístico verificando probabilístico, e mais uma
superfície de injection. Contra o medo nº 3 do Raí, uma constraint de banco vale mais que um
juiz estatístico.

### Aprovação por WhatsApp, com a Neuza respondendo "ok" — **descartada, com pena**

Era a melhor experiência possível para ela: aprovar sem sair do app onde já vive. Descartada
porque a autenticação seria a posse do número — e número de WhatsApp é clonável e transferível.
Aprovar uma reserva de R$ 250 mil não pode depender disso.

A solução de meio-termo está no [S-04](../spec/S-04-fila-de-aprovacao.md): a **notificação** vai
pelo WhatsApp, com link direto para uma tela mobile autenticada que abre no card certo. Dois
toques, sessão real. Perde um toque, ganha uma identidade verificável.

## Consequências

**Aceitas:**

- A Neuza é um ponto único de falha do fluxo comercial. Mitigado com escalonamento para o Raí
  após 15 minutos, e com meta de aprovação em ≤ 30s ([PRD §6.1](../PRD.md#61-negócio)).
- Nenhuma venda fecha 100% sozinha às 3h da manhã. Aceito e desejado: a Aurora qualifica, monta e
  deixa pronto; a Neuza aprova às 8h e o cliente recebe antes do café.
- Aprovação expirada gera retrabalho quando a Neuza demora. É o comportamento correto.
- A Aurora vai parecer limitada para quem pede desconto. É a intenção.

**Ganhas:**

- "Proposta sem aprovação" e "desconto pelo robô" deixam de ser risco a monitorar e viram
  estados **inalcançáveis** — um por constraint, outro por ausência de capacidade.
- A defesa contra prompt injection na etapa mais valiosa não depende do prompt.
