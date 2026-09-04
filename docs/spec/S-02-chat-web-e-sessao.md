# S-02 — Chat web e sessão de conversa

**Depende de:** [S-01](S-01-landing-e-captura-de-lead.md)
**Decide por:** [ADR-009](../adr/ADR-009-sem-framework-de-orquestracao.md)

---

## Objetivo

Interface de chat entre o cliente e a Aurora no site, com respostas em streaming, e o modelo de
dados de conversa que **serve aos dois canais** (web e WhatsApp) sem código duplicado.

## Comportamento

### 1. Modelo de dados

```
leads
  id, nome_cifrado, telefone_cifrado, telefone_hash (único),
  origem, criado_em, ultimo_acesso_em, apagar_em

conversas
  id, lead_id, etapa, canal_atual ('web' | 'whatsapp'),
  modo ('aurora' | 'humano'), atendente_id,
  qualificacao (jsonb), chassi_em_foco, trace_id,
  criada_em, ultima_mensagem_em

mensagens
  id, conversa_id, direcao ('entrada' | 'saida'), autor ('cliente' | 'aurora' | 'vendedor'),
  canal ('web' | 'whatsapp'), conteudo, gerada_por_ia (bool),
  whatsapp_message_id, payload_bruto (jsonb), criada_em
```

**`canal` é coluna da mensagem, não da conversa.** Uma conversa migra de canal e mantém id,
histórico e qualificação — é o que faz o handoff do [ADR-005](../adr/ADR-005-handoff-whatsapp-por-wa-me.md)
funcionar sem migração de sessão.

### 2. Etapas da conversa

`etapa` é a máquina de estados do [ADR-009](../adr/ADR-009-sem-framework-de-orquestracao.md), e
determina **quais tools existem** naquele turno.

| Etapa | Significa | Tools disponíveis |
|---|---|---|
| `saudacao` | Primeira mensagem | nenhuma |
| `qualificacao` | Descobrindo uso, rotina, orçamento | `registrar_qualificacao`, `buscar_conhecimento` |
| `recomendacao` | Apresentando e comparando | `buscar_unidades`, `detalhar_unidade`, `comparar_unidades`, `buscar_conhecimento` |
| `objecao` | Cliente com dúvida ou receio | as de `recomendacao` + `calcular_custo_km` |
| `condicao` | Cliente escolheu uma unidade; monta-se o espelho | `solicitar_aprovacao` |
| `aguardando_aprovacao` | Parado esperando a Neuza | **nenhuma** |
| `reserva` | Aprovado, reservando o chassi | `reservar_chassi` |
| `test_drive` | Agendando | `consultar_agenda`, `agendar_test_drive` |
| `humano` | Vendedor assumiu | **nenhuma — a Aurora não responde** |
| `encerrada` | Fim | nenhuma |

A transição é feita **pelo retorno de uma tool ou por regra de código**, nunca por o modelo
declarar que mudou de etapa. `transferir_para_humano` está disponível em todas as etapas exceto
`humano` e `encerrada`.

### 3. Streaming

`GET /api/conversas/{id}/stream` — Server-Sent Events, autenticado pelo cookie de sessão.

| Evento | Payload |
|---|---|
| `token` | fragmento de texto da resposta |
| `tool_inicio` | `{ "nome": "buscar_unidades" }` — a UI mostra "consultando o estoque…" |
| `tool_fim` | `{ "nome": "buscar_unidades", "resumo": "4 unidades" }` |
| `mensagem_fim` | `{ "mensagem_id": "…", "etapa": "recomendacao" }` |
| `aguardando` | `{ "motivo": "aprovacao" }` — a UI mostra o estado de espera |
| `erro` | `{ "codigo": "…", "mensagem_ao_cliente": "…" }` |

**A mensagem só é gravada e entregue depois da verificação numérica**
([ADR-003](../adr/ADR-003-numeros-nunca-saem-do-modelo.md)). O streaming de tokens é bufferizado
até a verificação passar: o cliente vê o indicador de digitação, não texto que pode ser retirado.

Reconexão: `Last-Event-ID` retoma do ponto. Queda de conexão não perde mensagem — o estado está no
Postgres.

### 4. Envio de mensagem

`POST /api/conversas/{id}/mensagens` com `{ "conteudo": "..." }`.

| Regra | Valor |
|---|---|
| Tamanho máximo | 2.000 caracteres |
| Turnos por conversa | 60 (depois: handoff automático para humano) |
| Mensagens por minuto | 12 por conversa |
| Mensagem vazia ou só espaço | `400`, não cria turno |

Se o cliente enviar durante um turno em andamento, a mensagem é enfileirada e processada no turno
seguinte — nunca dois turnos concorrentes na mesma conversa (lock por `conversa_id` no Redis).

### 5. Interface

- Bolhas distintas para cliente e Aurora; a Aurora tem avatar e o nome visível.
- Indicador "Aurora está consultando o estoque…" durante `tool_inicio`.
- Estado de espera de aprovação com texto honesto: *"Estou confirmando a condição com a nossa
  gerente. Volto em instantes — pode deixar essa janela aberta."*
- Botão **"falar com uma pessoa"** sempre visível.
- Ao entrar em `test_drive` ou quando a Aurora oferece, o botão de handoff para WhatsApp
  ([S-06](S-06-handoff-whatsapp.md)).
- Rolagem automática só quando o usuário já está no fim; nunca sequestra a rolagem de quem subiu.

### 6. Retomada

Reabrir a página com o cookie válido restaura a conversa aberta mais recente com todo o histórico.
Cookie expirado leva à landing com a mensagem *"Que bom te ver de novo"* e reconhece o lead pelo
`telefone_hash` no cadastro.

---

## Critérios de aceite

```gherkin
Cenário: resposta chega em streaming
  Quando envio "quero um elétrico até 150 mil"
  Então recebo eventos "token" progressivamente
  E um evento "mensagem_fim" com a etapa atualizada
  E a mensagem fica gravada em "mensagens" com gerada_por_ia = true

Cenário: consulta ao estoque fica visível para o cliente
  Quando a Aurora chama "buscar_unidades"
  Então recebo "tool_inicio" antes e "tool_fim" depois
  E a interface mostra "consultando o estoque"

Cenário: nenhum texto não verificado chega ao cliente
  Dado que a verificação numérica vai reprovar a resposta gerada
  Quando o turno é processado
  Então nenhum evento "token" com o texto reprovado foi enviado
  E a mensagem não existe na tabela "mensagens"

Cenário: dois envios simultâneos não geram dois turnos
  Quando envio duas mensagens no mesmo instante
  Então apenas um turno é processado por vez
  E a segunda mensagem entra no turno seguinte

Cenário: sem tools na etapa errada
  Dado que a conversa está em "aguardando_aprovacao"
  Quando o turno é montado
  Então a lista de tools enviada ao modelo está vazia

Cenário: o histórico atravessa o canal
  Dado uma conversa com 6 mensagens no canal "web"
  Quando ela migra para "whatsapp"
  Então o id da conversa é o mesmo
  E as 6 mensagens continuam no histórico do turno seguinte
```

## Fora do escopo

- Envio de áudio, imagem ou arquivo pelo cliente.
- Edição ou exclusão de mensagem enviada.
- Indicador de "lida" no chat web.
- Múltiplas conversas abertas simultâneas para o mesmo lead.
