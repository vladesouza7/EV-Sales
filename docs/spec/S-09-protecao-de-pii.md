# S-09 — Proteção de PII

**Transversal** — atravessa S-01 a S-10
**Decide por:** [ADR-007](../adr/ADR-007-pii-cifrada-e-mascarada.md)

---

## Objetivo

Que o nome e o telefone do cliente da Sol & Volt não existam em texto claro em nenhum lugar que
alguém leia por acidente — log, trace, mensagem de erro, prompt ou dump de banco.

## Comportamento

### 1. O que é PII neste projeto

| Dado | Coletado? | Tratamento |
|---|---|---|
| Nome | sim | Cifrado em repouso, mascarado em log e trace |
| Telefone | sim | Cifrado + hash indexável, mascarado, **nunca no prompt** |
| Conteúdo da conversa | sim | Redigido antes do prompt; guardado como o cliente escreveu |
| CPF, e-mail, endereço, CNH | **não** | Se aparecer na conversa, é redigido antes do prompt |
| Chassi | sim | Não é PII — é do carro, não da pessoa |

### 2. Cifragem

- **Algoritmo:** AES-256-GCM, nonce por registro.
- **Chave:** variável de ambiente `EVSALES_PII_KEY`, 32 bytes, base64. Nunca no Compose versionado,
  nunca em `.env.example` com valor real.
- **Colunas cifradas:** `leads.nome`, `leads.telefone`, `vendedores.telefone`.
- **`telefone_hash`:** HMAC-SHA256 com pepper em `EVSALES_PII_PEPPER` — variável **separada** da
  chave, para que vazar uma não entregue a outra.

**Decifrar acontece em exatamente três lugares**, e cada um é uma chamada explícita a
`decifrar_telefone()`, revisada em code review:

1. Montar o link `wa.me` ([S-06](S-06-handoff-whatsapp.md));
2. Enviar mensagem pela Evolution API;
3. Tela do vendedor autenticado.

Nenhum outro caminho decifra. O `__repr__` do modelo `Lead` devolve a versão mascarada — para que o
descuido mais comum (`print(lead)`, `logger.info(f"{lead}")`) já saia protegido.

### 3. Mascaramento na origem

Aplicado na camada que constrói o registro, antes de ele sair do processo — não na configuração do
destino ([ADR-007](../adr/ADR-007-pii-cifrada-e-mascarada.md)):

| Entrada | Saída |
|---|---|
| `+5583988714471` | `(83) *****-4471` |
| `Tarcísio Nóbrega` | `Tarcísio N.` |
| `Tarcísio` | `Tarcísio` (nome único não é mascarado) |
| `tarcisio@email.com` | `[EMAIL-REMOVIDO]` |
| `000.000.000-00` | `[CPF-REMOVIDO]` |
| `ABC1D23` | `[PLACA-REMOVIDA]` |

Vale para: log de aplicação, span do Langfuse, mensagem de erro, stack trace, notificação para a
Neuza, `stdout` em qualquer ambiente.

### 4. Redação antes do prompt

Passo determinístico, roda em toda mensagem do cliente antes de montar o contexto do modelo:

1. Substitui CPF, CNPJ, e-mail, placa e cartão por marcadores.
2. **O telefone nunca entra no prompt.** A Aurora não precisa dele; as tools usam `lead_id`.
3. O nome entra só como primeiro nome — é o vocativo, e basta.

O texto **original** é guardado em `mensagens.conteudo`, porque o Raí precisa ler a conversa como
ela aconteceu ([S-08](S-08-observabilidade-e-custo.md)). A redação é para o que **sai** da
infraestrutura da Sol & Volt.

No OpenRouter, o roteamento é restrito a provedores com retenção zero e sem treino sobre os dados
([ADR-008](../adr/ADR-008-openrouter-como-provedor.md)).

### 5. Acesso

| Perfil | Vê telefone completo | Vê a conversa | Aprova |
|---|---|---|---|
| `dono` (Raí) | sim | todas | sim (escalonamento) |
| `gerente` (Neuza) | sim | todas | **sim** |
| `vendedor` (Tarcísio, Jaqueline) | só dos leads atribuídos a ele | só dos seus | não |
| `sistema` | não — opera com hash | — | não |

Toda visualização de telefone completo grava linha de auditoria: quem, quando, qual lead.

### 6. Retenção

| Dado | Prazo | O que acontece |
|---|---|---|
| Lead sem nenhuma mensagem | 90 dias | Nome e telefone apagados; conversa anonimizada ([ADR-010](../adr/ADR-010-cadastro-antes-do-chat.md)) |
| Lead com conversa, sem venda | 24 meses | Idem |
| Lead que comprou | conforme obrigação fiscal | Mantido |
| Trace no Langfuse | 12 meses | Purga automática |
| Pedido de exclusão do titular | 15 dias | Apaga PII; mantém dado transacional anonimizado |

### 7. O teste que sustenta tudo isso

No CI, obrigatório:

1. Roda um atendimento completo de ponta a ponta com dados sintéticos conhecidos.
2. Captura `stdout`, `stderr`, arquivos de log e o banco de traces.
3. **Falha se encontrar** o telefone em qualquer formato (com e sem máscara, com e sem `+55`) ou o
   sobrenome completo.

É a única coisa que faz esta spec sobreviver a seis meses de commits — meus e de qualquer agente de
código que trabalhe neste repositório.

---

## Critérios de aceite

```gherkin
Cenário: telefone não fica em claro no banco
  Quando um lead é criado
  Então a coluna telefone contém ciphertext
  E telefone_hash é determinístico para o mesmo número

Cenário: o descuido mais comum já sai protegido
  Quando o código executa logger.info(f"{lead}")
  Então o log contém "(83) *****-4471"
  E não contém "988714471"

Cenário: o telefone não entra no prompt
  Quando um turno é montado
  Então o prompt enviado ao modelo não contém o telefone em nenhum formato

Cenário: CPF dito na conversa não sai da infraestrutura
  Quando o cliente escreve "meu CPF é 000.000.000-00"
  Então o prompt contém "[CPF-REMOVIDO]"
  E mensagens.conteudo guarda o texto original

Cenário: vendedor não vê lead de outro vendedor
  Dado um lead atribuído a Jaqueline
  Quando Tarcísio tenta abrir
  Então recebe 403

Cenário: ver telefone completo gera auditoria
  Quando a Neuza visualiza o telefone de um lead
  Então uma linha de auditoria é gravada com usuário, lead e horário

Cenário: lead abandonado é apagado
  Dado um lead com 91 dias e nenhuma mensagem
  Quando a rotina de retenção roda
  Então nome e telefone são apagados
  E a conversa permanece anonimizada

Cenário: o teste de varredura pega o vazamento
  Dado um código que loga o telefone em claro
  Quando o teste de PII roda no CI
  Então ele falha e aponta o arquivo e a linha
```

## Fora do escopo

- Cifragem em nível de disco ou TDE do Postgres — complementar, não substitui.
- Anonimização de conversa para treinar modelo.
- Consentimento granular por finalidade — o v1 tem uma finalidade só.
- Portal de autoatendimento LGPD — o pedido chega pelo WhatsApp e é tratado manualmente.
