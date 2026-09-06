# S-01 — Landing da Sol & Volt e captura de lead

**Depende de:** —
**Decide por:** [ADR-010](../adr/ADR-010-cadastro-antes-do-chat.md), [ADR-007](../adr/ADR-007-pii-cifrada-e-mascarada.md)

---

## Objetivo

Página pública da Sol & Volt que captura **nome e telefone**, cria o lead e abre a sessão de chat
com a Aurora. Oferece uma saída pelo catálogo somente-leitura para quem não quer se cadastrar.

## Comportamento

### 1. Estrutura da página

Herói na dobra inicial e formulário logo abaixo, alcançável por qualquer botão do herói.
Conteúdo, nesta ordem:

1. Marca **Sol & Volt Veículos Elétricos** e a linha "Tambaú, João Pessoa".
2. Chamada: *"Descubra qual elétrico combina com a sua rotina — em 3 minutos, conversando."*
   As duas primeiras linhas ficam no `h1`; o *"em 3 minutos, conversando"* fecha na linha de apoio.
3. Quatro botões de ação, nesta ordem:

   | Botão | Vai para |
   |---|---|
   | **Comece agora** | o formulário, com o campo `nome` em foco |
   | **Conheça os modelos** | `/catalogo` — a saída pelo lado do [ADR-010 §2](../adr/ADR-010-cadastro-antes-do-chat.md) |
   | **Conheça as ofertas** | `/ofertas` — o mesmo estoque, do menor para o maior preço |
   | **Confira com um test-drive** | `/test-drive` ([S-07](S-07-test-drive.md)) |

   `/ofertas` **não tem preço promocional**: não existe desconto no sistema (invariante 2,
   [ADR-004](../adr/ADR-004-aprovacao-humana-no-irreversivel.md)), e "oferta" aqui é ordenação
   por preço, não abatimento. É a mesma tela do catálogo, lendo a mesma tool.
4. Card da Aurora, com o texto exato de [ADR-010 §1](../adr/ADR-010-cadastro-antes-do-chat.md#1-o-formulário-entrega-valor-antes-de-pedir).
5. Formulário: `nome`, `telefone`, botão **"Conversar com a Aurora"**, e sob ele a nota de
   consentimento: *"Ao continuar, você concorda em receber nosso contato pode ficar tranquilo(a)
   não enviaremos spam ou propagandas desnecessárias."*

   > O link *"prefiro só olhar os carros"* saiu daqui. A mitigação do ADR-010 §2 continua —
   > catálogo somente-leitura, sem chat e sem cadastro — mas agora é o botão **"Conheça os
   > modelos"**, no herói, mais visível do que o link discreto que ele substitui. O ADR não muda:
   > a decisão que ele registra é que a saída existe, não onde ela fica.
6. Rodapé, em duas linhas: o aviso de privacidade (*"Guardamos só seu nome e WhatsApp. Se pedir,
   apagamos."*) e a assinatura da casa (*"VLADETEC © 2026 - EV-Sales…"*).

### 2. Campo `nome`

| | |
|---|---|
| Obrigatório | sim |
| Mínimo | 2 caracteres |
| Máximo | 80 caracteres |
| Aceita | letras (com acento), espaço, apóstrofo, hífen |
| Rejeita | dígitos, e-mail, URL |
| Normalização | `strip`, colapsa espaços internos. **Não** força capitalização |

Um único nome é válido: "Tarcísio" basta. A Aurora usa o primeiro token como vocativo.

### 3. Campo `telefone`

| | |
|---|---|
| Obrigatório | sim |
| Máscara de entrada | `(00) 00000-0000` |
| Normalização | descarta tudo que não é dígito; se vier com `55` na frente e 13 dígitos, remove o `55` |
| Formato válido | 11 dígitos: DDD (2) + `9` + 8 dígitos |
| DDD | deve existir na lista de DDDs brasileiros válidos |
| Nono dígito | obrigatoriamente `9` — a Sol & Volt só opera por WhatsApp |
| Armazenamento | `E.164` (`+5583988714471`), cifrado — ver [S-09](S-09-protecao-de-pii.md) |

Fixo (8 dígitos ou nono dígito diferente de 9) é rejeitado com: *"Precisa ser um celular com
WhatsApp — é por lá que a gente continua a conversa."*

**Não há verificação por SMS.** O número é verificado pela posse do aparelho no handoff
([ADR-005](../adr/ADR-005-handoff-whatsapp-por-wa-me.md)).

### 4. Envio

`POST /api/leads`

```json
{ "nome": "Tarcísio", "telefone": "+5583988714471", "origem": "landing" }
```

Ao receber:

1. Calcula `telefone_hash` (HMAC-SHA256 + pepper).
2. **Se já existe lead com esse hash:** reutiliza o lead, atualiza `ultimo_acesso_em`, e cria uma
   conversa nova vinculada a ele. Nunca duplica lead por telefone.
3. Cifra `nome` e `telefone`, grava, cria a conversa com `etapa = 'saudacao'`.
4. Responde `201` com `{ "conversa_id": "...", "token_sessao": "..." }`.

O `token_sessao` é opaco, válido por 24h, guardado em cookie `HttpOnly` + `SameSite=Lax`.

### 5. Rate limit

| Escopo | Limite |
|---|---|
| Por IP | 5 leads / 10 min |
| Por `telefone_hash` | 3 conversas / hora |

Excedido: `429` com *"Você já tem uma conversa aberta com a Aurora. Continue por lá."* e link para
a conversa ativa.

### 6. Saída pelo catálogo — `/catalogo`

Somente leitura, sem chat, sem cadastro. Lista as unidades com `status = 'disponivel'`, lendo
**as mesmas tools do agente** ([S-03](S-03-agente-aurora.md)) — nunca uma consulta paralela, para
que catálogo e Aurora nunca discordem.

Por unidade: modelo, versão, ano, cor, preço, autonomia com a fonte visível ("372 km — Inmetro"),
foto. Cada card tem **"falar com a Aurora sobre este"**, que leva ao formulário com
`?interesse=<chassi>` — e a Aurora abre a conversa já sabendo do que se trata.

### 7. Acessibilidade e dispositivo

Mobile-first: 71% do tráfego da Sol & Volt é celular. `<label>` real em cada campo, foco visível,
erro anunciado por `aria-live`, contraste mínimo AA. `inputmode="tel"` no telefone.

---

## Critérios de aceite

```gherkin
Cenário: cadastro válido abre a conversa
  Dado que estou na landing
  Quando preencho "Tarcísio" e "(83) 98871-4471" e envio
  Então um lead é criado com nome e telefone cifrados
  E uma conversa é criada com etapa "saudacao"
  E sou levado ao chat com a primeira mensagem da Aurora visível

Cenário: telefone fixo é recusado com orientação
  Quando preencho o telefone "(83) 3244-1010"
  Então vejo "Precisa ser um celular com WhatsApp"
  E nenhum lead é criado

Cenário: cliente que volta não vira lead duplicado
  Dado que existe um lead com o telefone "+5583988714471"
  Quando me cadastro de novo com o mesmo telefone
  Então o mesmo lead é reutilizado
  E uma conversa nova é criada vinculada a ele

Cenário: quem não quer se cadastrar vê o catálogo
  Quando clico em "prefiro só olhar os carros"
  Então vejo as unidades disponíveis com preço e autonomia
  E nenhum lead é criado
  E não há campo de chat na página

Cenário: catálogo e Aurora nunca discordam
  Dado que o chassi "…4471" foi marcado como reservado
  Quando abro /catalogo
  Então aquela unidade não aparece na listagem

Cenário: PII não aparece em log
  Quando um lead é criado
  Então o log da requisição contém "(83) *****-4471"
  E não contém "988714471" em nenhum lugar
```

## Fora do escopo

- Verificação por SMS ou código — resolvida no handoff.
- Login, senha, conta de cliente.
- Captura de e-mail, CPF ou endereço ([ADR-007 §1](../adr/ADR-007-pii-cifrada-e-mascarada.md#1-minimização--o-dado-que-não-é-coletado-não-vaza)).
- Teste A/B de chat aberto — hipótese registrada em [ADR-010](../adr/ADR-010-cadastro-antes-do-chat.md).
