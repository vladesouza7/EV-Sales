# S-06 — Handoff para o WhatsApp e continuidade da conversa

**Depende de:** [S-02](S-02-chat-web-e-sessao.md), [S-12](S-12-configuracoes.md) (o número e a chave)
**Decide por:** [ADR-005](../adr/ADR-005-handoff-whatsapp-por-wa-me.md)
**Estado:** ◐ parcial — §1 a §7 sim; a retentativa com backoff da §8 espera a fila do worker

| § | O quê | Estado |
|---|---|---|
| §1 | Quando a Aurora oferece | ◐ o botão, sempre visível; o gatilho por etapa não |
| §2 | Token de migração | ✔ `tokens_migracao`, 6 caracteres, uso único, 30 min |
| §3 | O link `wa.me` | ✔ |
| §4 | Webhook e recepção | ✔ autenticado, deduplicado por índice único, só enfileira |
| §5 | Continuidade, e a aba web que fecha | ✔ |
| §6 | Regras de envio | ◐ as três recusas sim; o intervalo de 3 s espera a fila |
| §7 | Formatação | ✔ |
| §8 | Falha da Evolution | ◐ incidente sim; as 3 tentativas com backoff não |

> **O token tem 6 caracteres, e os exemplos desta spec mostram `SV-7K2M`, que tem 4.** A §2
> é o modelo de dados e vence; 32⁶ contra 32⁴ é a diferença entre um código que não se
> adivinha e um que se adivinha em algumas horas — e adivinhar um token válido é amarrar o
> próprio número à conversa de outra pessoa. Os exemplos ficam como estão até alguém
> decidir, porque corrigir o [ADR-005](../adr/ADR-005-handoff-whatsapp-por-wa-me.md) exige
> revisão humana.

---

## Objetivo

Migrar a conversa do site para o WhatsApp **sem o cliente repetir nada** — e sem a Sol & Volt
enviar a primeira mensagem, o que protege o número comercial da loja.

## Comportamento

### 1. Quando a Aurora oferece

| Gatilho | Momento |
|---|---|
| Entrou em `recomendacao` e o cliente demonstrou interesse concreto | oferece uma vez |
| Vai entrar em `aguardando_aprovacao` | oferece — a espera é melhor no WhatsApp |
| Cliente pede test drive | oferece |
| Cliente pergunta se pode continuar por WhatsApp | imediato |
| Cliente já recusou uma vez | **não oferece de novo** — o botão continua visível na interface |

A oferta é uma frase, nunca um bloqueio:

> "Quer continuar no WhatsApp? Assim você não perde a conversa se fechar a página — e eu te aviso
> por lá quando a Neuza responder."

### 2. O token de migração

```
tokens_migracao
  token (PK, 6 caracteres, alfabeto sem ambiguidade: sem O/0/I/1),
  conversa_id, criado_em, expira_em, usado_em, telefone_hash_esperado
```

Uso único, validade **30 minutos**. Gerado quando o cliente aceita.

### 3. O link

```
https://wa.me/5583XXXXXXXXX?text=Oi%2C%20sou%20o%20Tarc%C3%ADsio.%20Vim%20do%20site%20—%20c%C3%B3digo%20SV-7K2M
```

- Número: a instância dedicada da Sol & Volt, **nunca** o celular pessoal do Raí.
- Texto pré-preenchido com o primeiro nome e o código. O cliente pode editar — o código é
  reconhecido em qualquer posição da mensagem.
- Mobile: abre o app. Desktop: abre o WhatsApp Web, com QR code como alternativa.

**A Sol & Volt não envia nada antes disso.** Não há caminho de código que envie mensagem para um
`telefone_hash` sem mensagem de entrada registrada — verificado por teste
([ADR-005](../adr/ADR-005-handoff-whatsapp-por-wa-me.md)).

### 4. Recepção do webhook

`POST /api/webhooks/evolution`, autenticado por chave no header e validado por IP de origem.

O endpoint **só enfileira e responde `200`**. Todo o processamento é do worker — webhook que
processa é webhook que estoura o timeout e faz a Evolution reenviar.

Fluxo no worker:

1. Deduplica por `whatsapp_message_id` (a Evolution reenvia; entrega é *at-least-once*).
2. Extrai o número, calcula `telefone_hash`.
3. **Procura código de migração** na mensagem (`SV-XXXX`):
   - **Achou, válido e não usado:** vincula a conversa àquele `conversa_id`, marca o token,
     grava `telefone_hash` verificado no lead, muda `canal_atual` para `whatsapp`.
   - **Achou, mas expirado:** cai no passo 4 com um aviso no trace.
4. **Sem código válido:** procura lead por `telefone_hash`.
   - **Achou, com conversa ativa (< 72h):** continua aquela conversa.
   - **Achou, sem conversa ativa:** abre conversa nova, `origem = 'whatsapp_retorno'`.
   - **Não achou:** abre lead e conversa novos, `origem = 'whatsapp_direto'` — alguém que achou o
     número no Instagram. A Aurora atende normalmente, começando pela saudação.
5. Grava a mensagem com `canal = 'whatsapp'` e o payload bruto em `payload_bruto`.
6. Processa o turno — **a mesma função do chat web**, mesmo `conversa_id`
   ([ADR-009](../adr/ADR-009-sem-framework-de-orquestracao.md)).

### 5. Continuidade

Quando a conversa migra, a Aurora **não recomeça e não recapitula por extenso**. Ela retoma:

> "Oi, Tarcísio! Continuando de onde a gente parou: você estava vendo o Seal branco, R$ 249.990.
> Quer que eu já veja um horário de test drive?"

O histórico do canal `web` está no contexto do turno, porque as mensagens são da mesma conversa.

**No chat web**, a janela mostra: *"Conversa migrada para o WhatsApp"*, e o campo de envio é
desabilitado. Duas janelas ativas na mesma conversa gerariam turnos concorrentes e respostas
duplicadas.

### 6. Regras de envio

| Regra | Valor | Onde é aplicada |
|---|---|---|
| Só responde dentro de conversa iniciada pelo cliente | sempre | código, sem exceção |
| Janela após a última mensagem do cliente | 24h | código |
| Mensagens consecutivas sem resposta | máx. 2, depois para | código |
| Intervalo mínimo entre mensagens de saída | 3s | fila do worker |
| Mensagem dividida em várias bolhas | máx. 3 | formatação |
| Follow-up automático | não existe no v1 | — |

Fora da janela de 24h, a conversa entra na fila do vendedor para contato humano.

### 7. Formatação

WhatsApp não tem Markdown completo. A camada de saída converte: `**negrito**` → `*negrito*`,
remove tabela e cabeçalho, quebra listas em linhas com `•`. Mensagem acima de 900 caracteres é
dividida em até 3 partes, cortando em parágrafo.

### 8. Falha da Evolution API

| Situação | Resposta |
|---|---|
| Instância desconectada | Alerta para mim e para o Raí; mensagens ficam na fila |
| Envio falha | 3 tentativas com backoff (5s, 30s, 2min) |
| Falhou as 3 | Marca `falha_envio`, notifica o vendedor, registra incidente |
| Fila > 50 pendentes | Alerta de saturação |

Mensagem do cliente **nunca** é perdida: ela é gravada antes de qualquer tentativa de processar.

---

## Critérios de aceite

```gherkin
Cenário: a loja nunca envia primeiro
  Dado um lead cadastrado com telefone e sem nenhuma mensagem recebida
  Quando qualquer rotina do sistema roda
  Então nenhuma mensagem é enviada para aquele número

Cenário: o código migra a conversa sem repetir nada
  Dado uma conversa web com 6 mensagens e o token "SV-7K2M"
  Quando chega uma mensagem de WhatsApp contendo "SV-7K2M"
  Então a mesma conversa continua, com o mesmo id
  E canal_atual vira "whatsapp"
  E a resposta da Aurora referencia o carro discutido no chat web
  E o token é marcado como usado

Cenário: token de uso único não migra duas vezes
  Dado o token "SV-7K2M" já usado
  Quando outra mensagem chega com o mesmo código
  Então a conversa não é revinculada
  E o remetente é tratado pelo telefone_hash

Cenário: webhook duplicado não gera resposta dupla
  Quando o mesmo whatsapp_message_id chega duas vezes
  Então apenas um turno é processado
  E apenas uma mensagem de saída é enviada

Cenário: cliente que chega direto pelo WhatsApp é atendido
  Dado um número sem lead cadastrado
  Quando ele manda "vocês têm elétrico até 150 mil?"
  Então um lead com origem "whatsapp_direto" é criado
  E a Aurora responde normalmente

Cenário: o webhook não processa, só enfileira
  Quando o webhook recebe uma mensagem
  Então responde 200 em menos de 200ms
  E o turno é processado pelo worker

Cenário: a janela de 24h é respeitada
  Dado que a última mensagem do cliente foi há 25 horas
  Quando o sistema tenta enviar
  Então o envio é bloqueado
  E a conversa entra na fila do vendedor

Cenário: web fecha quando o WhatsApp assume
  Dado que a conversa migrou
  Quando abro a janela do chat web
  Então vejo "Conversa migrada para o WhatsApp"
  E não consigo enviar mensagem
```

## Fora do escopo

- WhatsApp Cloud API oficial ([ADR-005](../adr/ADR-005-handoff-whatsapp-por-wa-me.md)) — v2.
- Áudio, imagem e documento recebidos do cliente.
- Grupos, listas de transmissão, status.
- Follow-up e reengajamento automático.
