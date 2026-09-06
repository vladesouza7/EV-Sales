# ADR-014 — Credencial e número de contato saem do `.env` e vão para o banco, cifrados

**Status:** proposto
**Data:** 2026-09-06
**Estende:** [ADR-012](ADR-012-provedor-configuravel.md) — que decidiu que o provedor é
configuração, e continua valendo; muda **onde** essa configuração mora
**Toca em:** [ADR-007](ADR-007-pii-cifrada-e-mascarada.md), [ADR-005](ADR-005-handoff-whatsapp-por-wa-me.md), [S-10](../spec/S-10-operacao.md), [S-11](../spec/S-11-autenticacao-e-perfis.md)

---

## Contexto

O [ADR-012](ADR-012-provedor-configuravel.md) tirou o provedor do código e botou no `.env`. Estava
certo, e resolveu o problema que existia naquele dia: **quem trocava o provedor era eu.**

A [S-06](../spec/S-06-handoff-whatsapp.md) chega com três valores novos que ninguém consegue chamar
de código — URL da Evolution, chave da instância e o número da Sol & Volt — e com isso a lista de
coisas que só mudam por `ssh` fica assim:

| O que muda | Com que frequência | Quem precisa mudar |
|---|---|---|
| Chave do provedor de LLM | quando expira, quando o crédito acaba, quando troca de provedor | Raí |
| Modelo | a cada eval que aprove um modelo melhor | eu |
| Número da instância do WhatsApp | quando a loja troca de chip ou de instância | Raí |
| Chave da Evolution | quando a instância é recriada | Raí |
| WhatsApp de quem recebe aviso | quando a Neuza troca de celular | Neuza |

Hoje, cada uma dessas linhas é: entrar no servidor, editar um arquivo com `vi`, subir o compose de
novo. A [S-10](../spec/S-10-operacao.md) tem como objetivo declarado **"que alguém que não sou eu
suba o EV-Sales, sem me perguntar nada"** — e a mesma frase vale para operar. Uma chave que vence
num sábado hoje só volta com alguém que saiba `docker compose up -d`, e a Sol & Volt não tem essa
pessoa. Não é conforto: é o [ADR-012](ADR-012-provedor-configuravel.md) prometendo que o sistema
não depende de uma conta em intermediário e, ao mesmo tempo, exigindo um deploy para trocar essa
conta.

## Decisão

**A configuração operacional passa a morar numa tabela `configuracoes`, cifrada em repouso, editável
por uma tela do perfil `dono`. O `.env` continua existindo como bootstrap e como último recurso, e
o banco vence.**

### 1. A chave fechada — o que impede a tela de virar um `settings` genérico

`configuracoes` é `(chave, valor_cifrado, atualizado_em, atualizado_por)`, e **`chave` é um
`Enum` fechado no código**. Chave nova exige commit, migration não — mas exige commit, e é isso que
importa: sem essa restrição, a tela é o lugar onde alguém, um dia, cadastra `preco_do_seal` e
contorna a invariante 1 por um formulário.

O que entra:

| Grupo | Chaves |
|---|---|
| WhatsApp da loja | `whatsapp_numero`, `evolution_url`, `evolution_instancia`, `evolution_chave` |
| Provedor de LLM | `llm_provedor`, `llm_modelo`, `llm_url`, `llm_chave`, `llm_fallbacks` |

O que **nunca** entra, e a lista é tão importante quanto a de cima:

- **Preço, catálogo, autonomia, disponibilidade.** São do Postgres e vêm por tool
  ([ADR-003](ADR-003-numeros-nunca-saem-do-modelo.md)). Configuração não é catálogo.
- **Teto de custo, prazo de reserva, validade da aprovação.** São regra de negócio verificada por
  teste. Campo de formulário para um número que um teste protege é o teste virando decoração.
- **`EVSALES_PII_KEY`, `EVSALES_PII_PEPPER`, `EVSALES_JWT_SECRET`.** A chave que cifra a tabela não
  pode morar na tabela que ela cifra, e a [S-10 §5](../spec/S-10-operacao.md) exige que ela viva
  **fora do servidor**. Continuam no `.env`, e é por isso que o `.env` não morre.

### 2. Cifrada, sempre, com a cifra que já existe

Todo valor é gravado com o `cifrar()` do [ADR-007](ADR-007-pii-cifrada-e-mascarada.md) — AES-256-GCM,
nonce por registro. Inclusive os que não são segredo, como a URL da Evolution.

Cifrar tudo é mais simples do que cifrar o que é segredo: um caminho só, sem uma coluna `e_segredo`
para alguém esquecer de marcar na chave seguinte. O custo é não poder consultar por valor, e não há
nenhum lugar do sistema que precise disso.

### 3. O segredo não volta pela tela

**Não existe rota que devolva um valor de configuração em claro.** A tela mostra `••••4f2a` — os
quatro últimos caracteres — e um campo vazio para substituir.

Ler o segredo de volta por uma tela autenticada transformaria o JWT de 20 minutos
([S-11 §4](../spec/S-11-autenticacao-e-perfis.md)) na chave da conta do provedor: quem pegasse uma
sessão da Neuza no celular dela levaria junto a API-key. A tela existe para **trocar**, não para
consultar. Quem precisa do valor é o processo, e o processo lê do banco.

### 4. A precedência tem um lugar só

```
banco (configuracoes)  →  .env  →  o padrão do preset
```

Num módulo só, `app/configuracao.py`. Duas fontes para o mesmo valor é o tipo de coisa que fica
correta enquanto existe um leitor, e diverge no dia em que aparece o segundo — e a
[S-06](../spec/S-06-handoff-whatsapp.md) traz o segundo, que é o worker.

Com o banco fora do ar não há configuração: é o mesmo estado em que a
[S-10 §6](../spec/S-10-operacao.md) já coloca o sistema, porque sem Postgres não há catálogo, não
há conversa e não há reserva. O `.env` responde nesse caso, e é a única razão de ele continuar
sendo lido.

### 5. PII de pessoa não entra em `configuracoes`

O telefone da Neuza e o do Tarcísio são telefones **de pessoas**. Eles já têm casa:
`vendedores.telefone_cifrado` existe desde a [S-07](../spec/S-07-test-drive.md), e `usuarios` ganha
a coluna equivalente. A tela edita essas colunas; ela não cria uma segunda cópia do número dentro de
`configuracoes`.

Duas cópias de um telefone é como a [S-09](../spec/S-09-protecao-de-pii.md) morre: a rotina de
retenção apaga uma, a varredura cobre a outra, e a que sobra é a que ninguém lembrava que existia.

**`whatsapp_numero` é a exceção, e é exceção por não ser PII:** é o número comercial da Sol & Volt,
que a própria [S-06 §3](../spec/S-06-handoff-whatsapp.md) publica dentro de um link `wa.me` na
página. Número que o sistema imprime numa página pública não é dado pessoal a proteger.

### 6. Gravar testa antes

Salvar credencial que não funciona é derrubar a Aurora pela tela — e é o risco novo que este ADR
cria, então ele nasce mitigado. Antes de gravar, o servidor usa a credencial: uma chamada mínima ao
provedor, um `GET` de estado na instância da Evolution. Se não autenticar, **não grava**, e a tela
diz o que o provedor respondeu.

O teste é contra o valor que está sendo enviado, nunca contra o que já está guardado — e a resposta
do provedor entra na tela, não no log, porque mensagem de erro de autenticação costuma ecoar a
credencial.

### 7. Trocar deixa rastro

Toda escrita grava linha na trilha ([ADR-006](ADR-006-observabilidade-e-teto-de-custo.md)): quem,
quando, **qual chave**. Nunca o valor, nem o antigo nem o novo — nem mascarado. A trilha é lida
pelo Raí numa tela e por mim como trace, e um segredo que passe por ela passa pelos dois.

## Alternativas consideradas

### Manter tudo no `.env` e dar um botão de "recarregar" — **descartada**

Menor diff possível e nenhuma superfície nova. Perdeu porque não resolve o problema: continua sendo
preciso **editar um arquivo no servidor**, e é exatamente esse passo que a Sol & Volt não consegue
dar. O app escrever no próprio `.env` seria pior — o arquivo é gerado pelo
[`gerar-segredos.sh`](../../scripts/gerar-segredos.sh) e sobrescrito no deploy seguinte, então a
troca feita pela tela sumiria sem aviso.

### Guardar em claro, protegido pelo perfil `dono` — **descartada**

É a alternativa mais difícil de recusar, porque a coluna já estaria atrás de autenticação. Recusada
pelo argumento que o [ADR-007](ADR-007-pii-cifrada-e-mascarada.md) já usou para o telefone: **um
dump do banco é um artefato que circula.** Ele vai para backup, para uma máquina de restauração,
para um `pg_dump` que alguém abriu para investigar um bug. O perfil `dono` não protege nada disso, e
uma API-key em claro num dump é uma conta comprometida com data de descoberta indefinida.

### Um gerenciador de segredos (Vault, SOPS, secret do provedor de nuvem) — **descartada**

É o certo por livro e continua sendo o certo se um dia houver mais de um servidor. Recusado pelo
mesmo critério que a [S-10](../spec/S-10-operacao.md) usa para recusar Kubernetes: **uma loja, um
servidor.** Um Vault a mais é um serviço a mais para subir, fazer backup e explicar num runbook
escrito para quem não construiu o sistema — e ele resolveria, aqui, o mesmo problema que uma coluna
cifrada resolve.

### Tabela de configuração com chave livre, em JSON — **descartada**

Mais flexível, e a flexibilidade é o defeito. Chave livre significa que o comportamento do sistema
passa a depender de um valor que nenhum código nomeia, nenhum teste cobre e nenhuma spec descreve.
`Enum` fechado é o que mantém a tela sendo configuração em vez de virar uma porta lateral para o
domínio.

## Consequências

**Aceitas:**

- **Existe um lugar no banco onde uma credencial mora.** O backup do Postgres, que a
  [S-10 §5](../spec/S-10-operacao.md) já tratava como sensível, passa a ser mais sensível ainda — e
  continua inútil sem a `EVSALES_PII_KEY`, que segue fora do servidor.
- **Duas fontes para o mesmo valor.** Mitigado por um leitor só, mas é uma complexidade real, e ela
  aparece no dia em que alguém edita o `.env` e não entende por que nada mudou. A tela diz, em cada
  campo, de onde o valor em uso está vindo.
- **A configuração vira uma consulta por turno.** Cache em processo com validade curta; com o worker
  da [S-10 §1](../spec/S-10-operacao.md), a invalidação não cruza processos e a janela de
  divergência é o tempo do cache.
- **Alguém pode derrubar a Aurora pela tela.** Mitigado pelo §6, e não eliminado: credencial válida
  hoje pode ser revogada amanhã do outro lado. É o mesmo risco que já existe com o `.env`, agora com
  mais gente alcançando o botão.

**Ganhas:**

- Trocar a chave do provedor deixa de ser um deploy. Rotacionar uma chave exposta vira uma ação de
  30 segundos numa tela, em vez de uma tarefa que espera por mim.
- A [S-06](../spec/S-06-handoff-whatsapp.md) ganha onde guardar o que ela precisa, em vez de
  inventar três variáveis de ambiente no meio da entrega.
- O `.env.example` para de ser a documentação da operação: o que é operável tem tela, o que é
  segredo de infraestrutura fica no `.env` e some da vista de quem opera a loja.
