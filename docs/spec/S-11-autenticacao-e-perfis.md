# S-11 — Autenticação e perfis

**Depende de:** [S-01](S-01-landing-e-captura-de-lead.md) (o padrão de cookie e sessão já existe lá)
**Bloqueia:** [S-04](S-04-fila-de-aprovacao.md), [S-08 §4 e §5](S-08-observabilidade-e-custo.md), [S-07 §9](S-07-test-drive.md)
**Decide por:** [ADR-004](../adr/ADR-004-aprovacao-humana-no-irreversivel.md), [ADR-007](../adr/ADR-007-pii-cifrada-e-mascarada.md)
**Estado:** ✗ não começada — esta spec existe porque três entregas pediam "sessão autenticada" e nenhuma dizia o que isso é

---

## Objetivo

Quatro pessoas entram no sistema pelo celular. A Neuza aprova em menos de 30 segundos, o Raí lê
qualquer atendimento, e o Tarcísio vê só os leads dele — e toda vez que alguém vê um telefone
completo, fica registrado quem viu.

## Comportamento

### 1. São quatro pessoas, e isso decide o resto

Não é SaaS. É uma loja em Tambaú com **quatro usuários nomeados**:

| Quem | Perfil | Entra para |
|---|---|---|
| Raí | `dono` | Ler qualquer atendimento, ver custo, aprovar no escalonamento |
| Neuza | `gerente` | **Aprovar condição e reserva** — é o uso que define o desenho |
| Tarcísio | `vendedor` | Ver os leads dele, telefone dos leads dele, agenda dele |
| Jaqueline | `vendedor` | idem |

Quatro usuários é o número que justifica **não** ter: cadastro público, recuperação de senha por
e-mail, SSO, convite, papel configurável. Cada uma dessas coisas é código a manter para resolver um
problema que a Sol & Volt não tem.

### 2. Modelo de dados

```
usuarios
  id, nome, email, senha_hash,
  perfil ('dono' | 'gerente' | 'vendedor'),
  vendedor_id  → vendedores.id, NULL para dono e gerente,
  ativo, criado_em, senha_trocada_em,
  tentativas_falhas, bloqueado_ate

sessoes
  id, usuario_id, token_hash (UNIQUE, indexado),
  criada_em, expira_em, ultimo_uso_em,
  user_agent_resumo, revogada_em
```

Duas escolhas que não são detalhe:

**O token da sessão é opaco e mora no banco, não é JWT.** O `EVSALES_JWT_SECRET` está no `.env`
desde o primeiro commit e **deixa de ser usado** — se esta spec for aprovada, a variável sai. O
motivo é revogação: a Neuza esquece o celular no balcão do café e o Raí precisa derrubar aquela
sessão **agora**. Com JWT isso exige uma lista de bloqueio, que é um banco de sessões com outro
nome e menos honesto. Uma linha em `sessoes` com `revogada_em` preenchido resolve, e usa o mesmo
padrão que `conversas.token_sessao` já usa na [S-01](S-01-landing-e-captura-de-lead.md).

**O token é guardado como hash, nunca em claro** — `sha256`, procurado por igualdade, exatamente
como `leads.telefone_hash` ([ADR-007](../adr/ADR-007-pii-cifrada-e-mascarada.md)). Um dump do banco
não entrega sessão ativa de ninguém.

`vendedor_id` liga o usuário à linha em `vendedores` que a [S-07](S-07-test-drive.md) já criou. Não
há duas tabelas de pessoa: `vendedores` é quem atende, `usuarios` é quem faz login, e o Tarcísio é
os dois.

### 3. Login

`POST /entrar` com e-mail e senha. Uma página, dois campos, um botão.

| Regra | Valor | Por quê |
|---|---|---|
| Hash de senha | **argon2id** | Já está no ambiente, veio com o cliente do MinIO |
| Tamanho mínimo | **12 caracteres** | Quatro pessoas, senha criada uma vez pelo Raí |
| Tentativas por conta | **5 em 15 minutos** | Depois disso, `bloqueado_ate = agora + 15 min` |
| Tentativas por IP | **20 por minuto** | Reusa `dentro_do_limite` da [S-01](S-01-landing-e-captura-de-lead.md) |
| Resposta a falha | **sempre a mesma** | "E-mail ou senha incorretos." Nunca "esse e-mail não existe" |
| Tempo de resposta | verifica o hash **mesmo com e-mail inexistente** | Senão o tempo da resposta conta quem existe |

Conta bloqueada responde a mesma mensagem das outras falhas. Dizer "sua conta está bloqueada"
confirma que a conta existe para quem está tentando adivinhar.

### 4. A sessão

| Regra | Valor |
|---|---|
| Cookie | `ev_staff`, `HttpOnly`, `SameSite=Lax`, `Secure` no perfil `prod` |
| Token | 32 bytes de `secrets.token_urlsafe`, guardado como `sha256` |
| Inatividade | **20 minutos sem uso** encerra a sessão |
| Renovação | cada requisição autenticada empurra `expira_em` para `agora + 20 min` |
| Limite absoluto | **12 horas** desde `criada_em`, mesmo em uso contínuo |
| Revogação | `POST /sair` encerra a atual; o Raí encerra qualquer uma pela tela de usuários |

**Vinte minutos é a janela de exposição de um celular perdido**, e é o número que manda nesta
spec. Um aparelho esquecido no balcão do café vira acesso à fila de aprovação por vinte minutos,
não por um mês.

O preço é real e está aqui para ser lido: a aprovação chega por notificação, em horários
espalhados pelo dia, e a Neuza vai encontrar a tela de login **na maioria das vezes** em que tocar
no link. O orçamento de 30 segundos da [S-04](S-04-fila-de-aprovacao.md) passa a incluir digitar a
senha. Isso torna duas coisas obrigatórias, e não opcionais:

1. **O destino tem de sobreviver ao login** (§7). Cair na fila genérica depois de autenticar, e
   ter de achar o card certo, é o que transformaria 30 segundos em dois minutos.
2. **O campo de senha aceita preenchimento automático do gerenciador do celular** — `autocomplete`
   correto, sem bloqueio de colar. Bloquear colar em nome de segurança é o que faz a senha virar
   curta e digitável.

Vinte minutos também **casa com o relógio do pedido**: a [S-04 §2](S-04-fila-de-aprovacao.md) dá
`expira_em = agora + 20 min` ao pedido de aprovação. Sessão e pedido morrem na mesma escala, então
não existe o caso de uma sessão viva apontando para um pedido morto há horas.

O limite absoluto de 12 horas existe porque renovação a cada uso, sozinha, é sessão eterna: um
aparelho usado a cada dezenove minutos nunca expiraria. Doze horas é um expediente. **É a única
regra desta tabela que não foi pedida — diga se quer fora.**

Aprovar **não pede senha de novo** dentro da janela. A proteção do irreversível é a pausa humana
registrada — `decidido_por` e `decidido_em` em todo pedido ([ADR-004](../adr/ADR-004-aprovacao-humana-no-irreversivel.md)) —, não repetir a senha a cada toque.

### 5. O que cada perfil alcança

A tabela da [S-09 §5](S-09-protecao-de-pii.md) vira rota:

| Rota | `dono` | `gerente` | `vendedor` |
|---|---|---|---|
| `/aprovacoes` — fila da Neuza ([S-04](S-04-fila-de-aprovacao.md)) | ✔ escalonamento | ✔ | ✗ |
| `/atendimentos` — ler conversa ([S-08 §4](S-08-observabilidade-e-custo.md)) | ✔ todas | ✔ todas | ✔ **só as dos leads dele** |
| `/custo` ([S-08 §5](S-08-observabilidade-e-custo.md)) | ✔ | ✔ | ✗ |
| `/agenda` ([S-07](S-07-test-drive.md)) | ✔ todas | ✔ todas | ✔ só a dele |
| Ver telefone completo | ✔ | ✔ | ✔ só dos leads dele |
| `/usuarios` — criar, desativar, derrubar sessão | ✔ | ✗ | ✗ |

**O recorte do vendedor é query, não filtro de tela.** A consulta já sai do banco com
`WHERE vendedor_id = :usuario`. Filtrar depois de ler significa que os dados dos outros leads
chegaram a existir no processo — e é assim que um dia eles aparecem num log de erro.

Rota que o perfil não alcança devolve **404**, não 403. Responder "existe, mas não é seu" já é
dizer que existe — é a mesma regra que a [S-02](S-02-chat-web-e-sessao.md) usa para conversa de
outro cliente.

### 6. Ver telefone é evento registrado

A [S-09 §5](S-09-protecao-de-pii.md) exige: *"Toda visualização de telefone completo grava linha de
auditoria: quem, quando, qual lead."*

Registrado na `trilha` que a [S-08 §1](S-08-observabilidade-e-custo.md) já criou, com
`tipo = 'evento'` e `nome = 'telefone_visto'`, guardando `usuario_id`, `lead_id` e o horário.
**O telefone não entra no evento** — nem em claro, nem mascarado. O que se audita é o acesso, e
guardar o número dentro do registro de auditoria seria criar uma segunda cópia do dado exatamente
onde ela é mais fácil de esquecer.

Decifrar continua tendo os três chamadores autorizados que a [S-09 §3](S-09-protecao-de-pii.md)
nomeia. Esta spec não cria um quarto: a tela do vendedor **é** o terceiro.

### 7. O link de uso único da S-04

A notificação da Neuza traz `https://…/a/7K2M` ([S-04 §3](S-04-fila-de-aprovacao.md)). O link
**leva ao card certo, não substitui o login** — e com a sessão de 20 minutos da §4, passar pelo
login é o caminho **normal**, não a exceção. É por isso que o passo 1 abaixo é a parte que não
pode falhar:

1. Sem sessão → página de login, guardando o destino.
2. Depois de entrar → vai direto para aquele pedido.
3. O código é de uso único e expira junto com o pedido, em 20 minutos.
4. Perfil sem acesso a `/aprovacoes` → 404, mesmo com o link correto.

### 8. Criar usuário e trocar senha

`scripts/criar-usuario.py`, como o `gerar-segredos.sh`. Pede o e-mail, o perfil e a senha, e não
imprime a senha em lugar nenhum. Não há e-mail de convite porque não há servidor de e-mail no
compose ([S-10 §1](S-10-operacao.md)), e inventar um para quatro pessoas é caixa que a Sol & Volt
teria de manter.

Trocar senha: o próprio usuário em `/minha-senha`, informando a senha atual. Esqueceu: o Raí roda o
script. É o procedimento de uma loja com quatro funcionários, e está no runbook da
[S-10 §6](S-10-operacao.md).

Trocar a senha **revoga todas as sessões daquele usuário**, inclusive a que fez a troca. É o que
torna "perdi o celular" resolvível pelo próprio dono da conta, sem esperar o Raí.

---

## Critérios de aceite

```gherkin
Cenário: dentro da janela, aprovar não repede senha
  Dado que a Neuza usou o sistema há menos de 20 minutos
  Quando ela abre a fila de aprovação e toca em APROVAR
  Então a aprovação é registrada com "decidido_por" preenchido com o id dela
  E nenhuma senha foi pedida no caminho

Cenário: vinte minutos parada encerra a sessão
  Dado que a última requisição da Neuza foi há 21 minutos
  Quando ela abre a fila de aprovação
  Então a resposta é a página de login
  E a sessão anterior não é renovada

Cenário: uso contínuo renova, mas não para sempre
  Dado uma sessão criada há 11 horas e usada a cada 5 minutos
  Então ela continua valendo
  Mas uma hora depois, mesmo em uso, ela é recusada pelo limite de 12 horas

Cenário: expirar no meio não perde o destino
  Dado que a Neuza toca no link do WhatsApp com a sessão expirada
  Quando ela entra com a senha
  Então ela cai no card daquele pedido, não na fila genérica

Cenário: e-mail inexistente e senha errada respondem igual
  Quando alguém tenta entrar com um e-mail que não existe
  E alguém tenta entrar com um e-mail que existe e senha errada
  Então as duas respostas têm a mesma mensagem e o mesmo código de status
  E a diferença de tempo entre as duas é menor que 100 ms

Cenário: conta bloqueia depois de cinco tentativas
  Dado cinco tentativas falhas na conta da Neuza em menos de 15 minutos
  Quando a sexta tentativa acontece, mesmo com a senha correta
  Então o acesso é recusado
  E a resposta é a mesma mensagem das outras falhas

Cenário: o vendedor não vê o lead do outro vendedor
  Dado um lead atribuído à Jaqueline
  Quando o Tarcísio abre o atendimento desse lead
  Então a resposta é 404
  E a consulta ao banco já filtrou por vendedor_id

Cenário: ver telefone completo deixa rastro
  Quando a Neuza abre o telefone completo de um lead
  Então existe uma linha na trilha com nome "telefone_visto"
  E ela contém o id do usuário, o id do lead e o horário
  E não contém o telefone, nem em claro nem mascarado

Cenário: o link do WhatsApp não substitui o login
  Dado o link de uso único de um pedido de aprovação
  Quando ele é aberto sem sessão
  Então a resposta é a página de login
  E depois de entrar, a Neuza cai no card daquele pedido

Cenário: link de uso único não serve duas vezes
  Dado um link de aprovação já usado
  Quando ele é aberto de novo
  Então leva à fila, não ao card
  E nenhuma decisão é registrada

Cenário: trocar a senha derruba as sessões
  Dado que a Neuza tem sessão no celular e no computador
  Quando ela troca a senha pelo computador
  Então as duas sessões deixam de valer
  E ela precisa entrar de novo nos dois

Cenário: o Raí derruba a sessão de um celular perdido
  Dado que a Neuza perdeu o celular com sessão ativa
  Quando o Raí revoga aquela sessão em /usuarios
  Então a próxima requisição daquele celular é recusada
  E as outras sessões da Neuza continuam valendo

Cenário: o token da sessão não existe em claro no banco
  Quando uma sessão é criada
  Então nenhuma coluna de "sessoes" contém o valor enviado no cookie
  E a busca acontece pelo hash

Cenário: sessão revogada não ressuscita
  Dado uma sessão com revogada_em preenchido
  Quando o cookie dela é apresentado
  Então a resposta é 401
  E a sessão não é renovada
```

## Fora do escopo

- **Cadastro público e convite por e-mail.** São quatro pessoas, criadas por script. Não há
  servidor de e-mail no compose ([S-10 §1](S-10-operacao.md)).
- **Recuperação de senha por e-mail.** Pelo mesmo motivo. Esqueceu, o Raí roda o script — e o
  procedimento está no runbook.
- **Segundo fator (TOTP).** A restrição de 30 segundos da [S-04](S-04-fila-de-aprovacao.md) manda
  aqui. **Gatilho de revisão, com número:** entra quando houver mais de 10 usuários, ou no primeiro
  acesso a partir de fora da rede da loja.
- **SSO / OAuth.** Não há diretório corporativo na Sol & Volt.
- **Permissão granular por recurso.** Três perfis fixos em código. Perfil configurável em banco é
  a porta pela qual alguém, um dia, dá aprovação a um vendedor sem passar por revisão — e é
  exatamente o que o [ADR-004](../adr/ADR-004-aprovacao-humana-no-irreversivel.md) impede.
- **Sessão por dispositivo com nome amigável.** `user_agent_resumo` basta para o Raí reconhecer
  qual derrubar.
