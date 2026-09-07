# S-04 — Fila de aprovação da Neuza e o Espelho de Condição

**Depende de:** [S-03](S-03-agente-aurora.md), [S-11](S-11-autenticacao-e-perfis.md)
**Decide por:** [ADR-004](../adr/ADR-004-aprovacao-humana-no-irreversivel.md), [ADR-011](../adr/ADR-011-jornada-digital-termina-no-test-drive.md), [ADR-013](../adr/ADR-013-minio-para-arquivo-gerado.md)
**Estado:** ✔ implementada — o backend, a tela, a notificação por WhatsApp e o escalonamento.

| § | O quê | Estado |
|---|---|---|
| §1 | `pedidos_de_aprovacao` e `espelhos`, com `approval_id` NOT NULL | ✔ |
| §2 | A tool `solicitar_aprovacao`, que para o fluxo | ✔ `app/aprovacao.py` |
| §3 | Notificação da Neuza, escalonamento em 15 min | ✔ pela `avisar_equipe` da [S-06](S-06-handoff-whatsapp.md), com nome mascarado e sem telefone |
| §4 | A tela de decisão | ✔ `/aprovacoes`, com o login da [S-11](S-11-autenticacao-e-perfis.md) em `/entrar` |
| §5 | Emissão do Espelho, com relê de preço e PDF | ✔ `app/espelho.py`, PDF no MinIO ([ADR-013](../adr/ADR-013-minio-para-arquivo-gerado.md)) |
| §6 | Rejeição e expiração em 20 min | ✔ |
| §7 | Auditoria: quem, quando, de qual IP | ✔ na `trilha` e no próprio pedido |

**Uma diferença de esquema, e o motivo:** a coluna do documento chama-se `pdf_objeto`, e não
`pdf_url`. Com o [ADR-013](../adr/ADR-013-minio-para-arquivo-gerado.md) a URL do Espelho é
assinada e expira em 15 minutos — guardar uma URL na linha seria guardar um link morto. A linha
guarda o objeto, e a URL é emitida na hora em que alguém autorizado pede.

---

## Objetivo

A pausa antes do irreversível. A Aurora monta a condição e **para**; a Neuza decide pelo celular em
menos de 30 segundos; só então o documento existe e o chassi pode sair do estoque.

> **O que a Neuza aprova:** *"esse carro sai do estoque para esse cliente, por 72h, com esse preço
> escrito."* Não é aprovação de venda — a venda é fechada presencialmente, no sistema que a Sol &
> Volt já opera ([ADR-011](../adr/ADR-011-jornada-digital-termina-no-test-drive.md)).

O problema de design é um par de restrições opostas ([CASE](../CASE.md#quem-coloca-a-mão-no-sistema)):
a Neuza precisa decidir em segundos, entre dois atendimentos presenciais, com o celular na mão — e
condição que espera muito é venda perdida.

## Comportamento

### 1. Modelo de dados

```
pedidos_de_aprovacao
  id, conversa_id, chassi, lead_id,
  preco_centavos,            -- lido do Postgres no instante do pedido
  qualificacao_resumo (jsonb),
  status ('pendente' | 'aprovado' | 'rejeitado' | 'expirado'),
  decidido_por, decidido_em, motivo_rejeicao,
  trace_id, criado_em, expira_em

espelhos                     -- Espelho de Condição e Reserva. NÃO é contrato nem documento fiscal
  id, conversa_id, chassi,
  approval_id  NOT NULL REFERENCES pedidos_de_aprovacao(id),   -- ← a garantia
  preco_centavos, pdf_url, numero, valido_ate, emitido_em
```

O nome da tabela é `espelhos`, e não `propostas`, de propósito: a palavra "proposta" carregou a
ambiguidade que originou o [ADR-011](../adr/ADR-011-jornada-digital-termina-no-test-drive.md). Quem
implementar não deve construir um documento de venda.

`espelhos.approval_id` é `NOT NULL` com foreign key. **Não existe caminho de código que emita sem
aprovação** — se alguém (eu, ou um agente de código) escrever um `INSERT` sem ele, o banco recusa.
Um teste tenta exatamente isso e exige a recusa.

`preco_centavos` é preenchido **pelo servidor**, relendo `unidades.preco_centavos`. Não vem do
modelo, não vem do payload da requisição, não passa pelo agente
([ADR-004](../adr/ADR-004-aprovacao-humana-no-irreversivel.md#parte-2--desconto-não-é-proibido-ele-não-existe)).

### 2. Solicitação

A tool `solicitar_aprovacao(chassi)` faz, em uma transação:

1. Confere `unidades.status = 'disponivel'`. Se não estiver, devolve `indisponivel` e a Aurora
   retoma a recomendação sem parar a conversa.
2. Lê o preço **naquele instante**.
3. Cria o pedido com `expira_em = now() + 20 min`.
4. Muda a conversa para `aguardando_aprovacao` — etapa **sem tools** ([S-02 §2](S-02-chat-web-e-sessao.md#2-etapas-da-conversa)).
5. Dispara a notificação.

A Aurora diz ao cliente, com honestidade:

> "Estou confirmando essa condição com a nossa gerente. Volto em instantes — se preferir, pode me
> chamar no WhatsApp que eu te aviso por lá assim que ela responder."

**O turno termina normalmente.** Não há processo suspenso: o estado é a linha no banco
([ADR-009](../adr/ADR-009-sem-framework-de-orquestracao.md)).

### 3. Notificação

WhatsApp para o número da Neuza, pela mesma instância da Evolution API:

```
🔔 Reserva para aprovar — Sol & Volt

Tarcísio N.
BYD Seal Premium · branco · chassi …4471
R$ 249.990  ·  sai do estoque por 72h

Rodando 40 km/dia · cidade · tem carregador em casa
Orçamento informado: até R$ 260 mil

Decidir: https://evsales.solevolt.com.br/a/7K2M
```

Nome mascarado, telefone ausente ([ADR-007](../adr/ADR-007-pii-cifrada-e-mascarada.md)). O link é
de uso único, expira junto com o pedido e **exige sessão autenticada** — ele leva ao card certo,
não substitui o login ([ADR-004](../adr/ADR-004-aprovacao-humana-no-irreversivel.md#aprovação-por-whatsapp-com-a-neuza-respondendo-ok--descartada-com-pena)).

Sem decisão em **15 minutos**, a mesma notificação vai para o Raí, que também pode aprovar.

### 4. A tela de decisão

Feita para 30 segundos, no celular, em pé. Um card por pedido, **sem menu, sem filtro, sem
paginação**:

```
┌──────────────────────────────────┐
│ Tarcísio N.          há 40 seg   │
│                                  │
│ BYD Seal Premium                 │
│ branco · chassi …4471            │
│                                  │
│ R$ 249.990                       │
│ sai do estoque por 72h           │
│                                  │
│ 40 km/dia · cidade               │
│ carregador em casa · até 260 mil │
│                                  │
│  [  APROVAR  ]   [ recusar ]     │
│                                  │
│  ver a conversa inteira →        │
└──────────────────────────────────┘
```

| Regra | Valor |
|---|---|
| Alvo de toque | ≥ 48px, polegar alcança sem reposicionar |
| Aprovar | 1 toque, sem confirmação |
| Recusar | 1 toque + motivo em lista fixa (preço, unidade prometida, cliente conhecido, outro) |
| Ordenação | mais antigo primeiro |
| Atualização | tempo real, sem recarregar |
| Contagem | tempo desde o pedido, visível — pressiona pelo lado certo |

Aprovar não pede confirmação de propósito: confirmação dobra o tempo e treina o toque automático.
A reversão existe na tela do vendedor enquanto o cliente não recebeu o documento.

### 5. Emissão do Espelho

Na aprovação, em uma transação:

1. `status = 'aprovado'`, com `decidido_por` e `decidido_em`.
2. Relê `unidades.preco_centavos`. **Divergiu do pedido** (a Neuza mexeu na tabela nesse intervalo):
   aborta, marca `expirado`, notifica e refaz o pedido com o preço novo.
3. Gera o espelho com `approval_id`, número sequencial `SV-2026-0001` e PDF.
4. Conversa vai para `reserva` ([S-05](S-05-reserva-de-chassi.md)).

**O que o PDF contém:**

| Contém | **Não** contém |
|---|---|
| Identificação da Sol & Volt (CNPJ, endereço, telefone) | Condição de financiamento |
| Nome do cliente | Entrada, parcela, taxa, prazo de crédito |
| Modelo, versão, ano, cor, **chassi** | Dados de pagamento |
| Preço à vista, por extenso e em algarismos | Qualquer campo fiscal |
| Validade da condição: **7 dias corridos** | Assinatura ou aceite formal |
| Reserva do chassi: **72 horas** | Promessa de prazo de entrega |
| Data, hora e vendedor do test drive | |
| Nome de quem aprovou | |

E, no rodapé, sem eufemismo:

> *A negociação final, o financiamento e a documentação são tratados presencialmente na loja.*

Não há disclaimer de "documento simulado" porque não é preciso: um espelho de condição e reserva
**não é documento fiscal por natureza**, e é exatamente o artefato que a Neuza produz hoje à mão
([ADR-011](../adr/ADR-011-jornada-digital-termina-no-test-drive.md)).

### 6. Rejeição e expiração

**Rejeitado:** a Aurora não improvisa justificativa. Ela diz que a gerência precisa falar
diretamente e faz `transferir_para_humano`. O motivo da rejeição vai para o vendedor, nunca para o
cliente.

**Expirado (20 min):** o pedido é marcado, a Aurora avisa que vai reconfirmar o valor e refaz. O
cliente nunca recebe preço aprovado fora da validade.

### 7. Auditoria

Toda decisão grava: quem, quando, de qual IP, qual pedido, qual `trace_id`. Aparece na tela "Ler
atendimento" como a linha `⏸` ([ADR-006](../adr/ADR-006-observabilidade-e-teto-de-custo.md)).
Registro de aprovação **nunca** é apagado, nem quando a reserva é liberada.

---

## Critérios de aceite

```gherkin
Cenário: o banco recusa espelho sem aprovação
  Quando tento inserir um espelho com approval_id nulo
  Então o banco rejeita por violação de NOT NULL
  E nenhum espelho é criado

Cenário: a Aurora para e não emite sozinha
  Quando a Aurora chama "solicitar_aprovacao"
  Então a etapa vira "aguardando_aprovacao"
  E a lista de tools do turno seguinte está vazia
  E nenhum espelho foi criado

Cenário: o preço vem do banco, não do agente
  Dado que o agente sugeriu R$ 240.000 em texto
  E o Postgres registra R$ 249.990 para o chassi
  Quando a Neuza aprova
  Então preco_centavos do espelho é 24999000

Cenário: preço alterado durante a espera invalida a aprovação
  Dado um pedido criado com R$ 249.990
  E que a Neuza mudou a tabela para R$ 254.990 antes de decidir
  Quando ela aprova
  Então o pedido é marcado "expirado"
  E nenhum espelho é emitido
  E um pedido novo é criado com R$ 254.990

Cenário: expiração em 20 minutos
  Dado um pedido pendente criado há 21 minutos
  Então seu status é "expirado"
  E ele não aparece mais na fila da Neuza

Cenário: escalonamento para o Raí
  Dado um pedido pendente há 15 minutos sem decisão
  Então o Raí recebe a notificação
  E também pode aprovar

Cenário: o espelho não é documento de venda
  Quando um espelho é emitido
  Então o PDF não contém campo de financiamento, parcela ou taxa
  E não contém campo fiscal
  E contém a validade da condição e o prazo da reserva
  E contém a linha sobre a negociação final ser presencial

Cenário: rejeição não vira desculpa inventada
  Quando a Neuza recusa com motivo "unidade prometida"
  Então a conversa muda para modo "humano"
  E a última mensagem da Aurora não contém o motivo da recusa

Cenário: a notificação não vaza PII
  Quando a notificação é enviada
  Então contém "Tarcísio N."
  E não contém o sobrenome completo nem o telefone do cliente
```

## Fora do escopo

- Aprovação por resposta de WhatsApp ([ADR-004](../adr/ADR-004-aprovacao-humana-no-irreversivel.md)).
- **A aprovação da venda** — é presencial, fora do sistema ([ADR-011](../adr/ADR-011-jornada-digital-termina-no-test-drive.md)).
- Nota fiscal, faturamento, contrato, financiamento e assinatura eletrônica.
- Alçadas por valor ou por vendedor.
- Desconto na tela da Neuza durante a aprovação — ela altera a tabela, não o pedido.
