# S-12 — Configurações: credencial e número sem `ssh`

**Depende de:** [S-11](S-11-autenticacao-e-perfis.md) (o perfil `dono` e o recorte por rota)
**Bloqueia:** [S-06](S-06-handoff-whatsapp.md) — é aqui que o número e a chave da Evolution passam a existir
**Decide por:** [ADR-014](../adr/ADR-014-configuracao-operacional-no-banco.md), [ADR-007](../adr/ADR-007-pii-cifrada-e-mascarada.md), [ADR-012](../adr/ADR-012-provedor-configuravel.md)
**Estado:** ✔ implementada — 19 testes; a tela, as rotas, o leitor único e a migration

| § | O quê | Estado |
|---|---|---|
| §1 | Quem entra | ✔ |
| §2 | As nove chaves, e a validação de cada uma | ✔ |
| §3 | Quem recebe aviso — telefone de pessoa, que não mora aqui | ✔ |
| §4 | O segredo não volta pela tela | ✔ |
| §5 | Gravar testa antes, e o estado da instância | ◐ — a sonda sim; o estado na tela não |
| §6 | Precedência, cache e quando a troca passa a valer | ✔ |
| §7 | Rastro | ✔ |
| §8 | O que esta tela nunca configura | ✔ |
| §9 | A tela | ✔ |

---

## Objetivo

Que o Raí troque a chave do provedor e o número do WhatsApp da loja pelo celular, em menos de um
minuto, sem abrir terminal — e que nenhum desses valores volte para a tela em claro.

## Comportamento

### 1. Quem entra

Só o perfil `dono`. A Neuza aprova condição, o Tarcísio vê os leads dele, e nenhum dos dois alcança
credencial — é o recorte mais estreito possível, que é o certo para segredo.

| Rota | Sem sessão | Sessão `gerente` ou `vendedor` | Sessão `dono` |
|---|---|---|---|
| `GET /configuracoes` (HTML) | 200 | 200 | 200 |
| `GET /api/configuracoes` | 401 | **404** | 200 |
| `PUT /api/configuracoes` | 401 | **404** | 200 |
| `POST /api/configuracoes/testar` | 401 | **404** | 200 |

O HTML é público pelo mesmo motivo da tela de aprovações ([S-11 §5](S-11-autenticacao-e-perfis.md)):
a página não contém dado nenhum, quem barra é a API, e sem sessão ela manda para o login guardando o
destino. O 404 em vez de 403 também é da S-11: 403 conta que a tela existe.

### 2. As nove chaves

Tabela `configuracoes (chave, valor_cifrado, atualizado_em, atualizado_por)`. `chave` é `Enum`
fechado no código — chave nova exige commit ([ADR-014](../adr/ADR-014-configuracao-operacional-no-banco.md) §1).

| Chave | O que é | Sigilo | A tela mostra | Recusa |
|---|---|---|---|---|
| `whatsapp_numero` | Número da instância da Sol & Volt | não | `+55 83 99157-5299` | o que `normalizar_telefone` recusar |
| `evolution_url` | Base da Evolution API | não | inteira | fora de `http://` ou `https://` |
| `evolution_instancia` | Nome da instância | não | inteira | fora de `[a-z0-9][a-z0-9-]{0,63}` |
| `evolution_chave` | `apikey` da instância | **sim** | `••••4f2a` | vazia |
| `llm_provedor` | Qual preset do [ADR-012](../adr/ADR-012-provedor-configuravel.md) | não | seletor | nome fora de `PRESETS` |
| `llm_modelo` | Id exato do modelo, com versão | não | inteiro | vazio |
| `llm_url` | Sobrescreve a base do preset | não | inteira | vazia quando `llm_provedor=compativel` |
| `llm_chave` | API-key do provedor | **sim** | `••••4f2a` | vazia quando `preset.exige_chave` |
| `llm_fallbacks` | Dois modelos, na ordem de tentativa | não | inteira | mais de dois |

Todo valor é gravado cifrado, inclusive os que não são segredo — um caminho só, sem uma coluna
`e_segredo` para alguém esquecer de marcar na chave seguinte.

**O número da loja usa a mesma validação do cliente**, a da [S-01 §3](S-01-landing-e-captura-de-lead.md):
celular brasileiro em E.164, DDD que existe, nono dígito igual a 9. A instância da Sol & Volt é
`+5583991575299`, e ela precisa ser um celular pelo motivo do [ADR-005](../adr/ADR-005-handoff-whatsapp-por-wa-me.md):
é para lá que o cliente manda a **primeira** mensagem, pelo link `wa.me`.

`llm_fallbacks` só tem efeito no `openrouter`, e a tela diz isso ao lado do campo — é campo do
payload dele, não retentativa nossa ([ADR-012](../adr/ADR-012-provedor-configuravel.md)).

### 3. Quem recebe aviso — telefone de pessoa não mora aqui

A tela edita **quatro** telefones que não são chaves de configuração:

| Quem | Onde mora | Para quê |
|---|---|---|
| Neuza | `usuarios.telefone_cifrado` — coluna nova | A notificação de aprovação ([S-04 §3](S-04-fila-de-aprovacao.md)) |
| Raí | idem | O escalonamento de 15 min ([S-04 §3](S-04-fila-de-aprovacao.md)) e o alerta de 80 % do teto ([S-08 §3](S-08-observabilidade-e-custo.md)) |
| Tarcísio | `vendedores.telefone_cifrado` — já existe | Aviso de test drive ([S-07 §6](S-07-test-drive.md)) |
| Jaqueline | idem | idem |

A coluna é de `usuarios`, não uma lista de destinatários: **quem tem telefone recebe o que o perfil
dele manda receber**. Uma lista separada seria um segundo lugar dizendo quem é a gerente, e os dois
divergiriam no dia em que a Sol & Volt contratasse a segunda.

Isso também resolve uma pendência silenciosa: a [S-10 §6](S-10-operacao.md) manda "verificar
`NEUZA_WHATSAPP`" num runbook, e essa variável **nunca existiu** no `.env.example`. Agora ela tem
lugar, e a linha do runbook passa a apontar para a tela.

São telefones **de pessoas**, e ficam com as pessoas: uma segunda cópia dentro de `configuracoes`
seria a cópia que a retenção da [S-09 §6](S-09-protecao-de-pii.md) esquece de apagar
([ADR-014](../adr/ADR-014-configuracao-operacional-no-banco.md) §5).

Exibidos mascarados — `(83) *****-5299` —, gravados cifrados, validados por `normalizar_telefone`.
Ver o telefone completo de um vendedor continua sendo `GET /api/leads/{id}/telefone` e continua
gerando linha de auditoria ([S-11 §6](S-11-autenticacao-e-perfis.md)); **esta tela nunca mostra
telefone completo**, nem para o `dono`.

### 4. O segredo não volta pela tela

`GET /api/configuracoes` devolve, para cada chave:

```json
{
  "chave": "llm_chave",
  "resumo": "••••4f2a",
  "sigiloso": true,
  "fonte": "banco",
  "atualizado_em": "2026-09-06T14:22:10-03:00",
  "atualizado_por": "Raí Sol"
}
```

**Não existe rota, parâmetro ou modo que devolva `valor` em claro.** Nem para o `dono`, nem com
confirmação de senha, nem "só desta vez".

No `PUT`, campo ausente ou vazio significa **não mudou**. Não existe apagar por engano digitando
nada: para remover um valor, o corpo traz `{"chave": "llm_fallbacks", "limpar": true}`, explícito.

### 5. Gravar testa antes, e o estado da instância

`POST /api/configuracoes/testar` recebe os valores que estão na tela, **antes de gravar**, e usa:

| Grupo | O teste | Aprova quando |
|---|---|---|
| Provedor | Uma chamada de 1 token ao `/chat/completions` do preset | resposta 2xx |
| Evolution | `GET {evolution_url}/instance/fetchInstances` com a `apikey` | resposta 2xx |

`PUT` roda o mesmo teste, e **o que decide não é "falhou", é o motivo**:

| O provedor respondeu | O que acontece | Por quê |
|---|---|---|
| 2xx | grava | a credencial serve |
| **401 ou 403** | **não grava**, `422` na tela | a credencial foi recusada, e gravar seria derrubar a Aurora |
| 5xx, timeout, DNS | **grava**, com aviso na tela | é indisponibilidade, não recusa |

A terceira linha é a que faltava na primeira versão desta spec, e ela importa mais do que parece:
com "só grava se passar", uma instabilidade do provedor **tranca a tela justamente na hora em que
alguém precisa trocar de provedor**. O modo de falha que a tela existe para resolver seria o modo de
falha que a impede de funcionar.

Falhou por recusa: `422`, com a mensagem do provedor **na resposta e na tela, nunca no log** — erro
de autenticação costuma ecoar a credencial que o causou, e log é uma das quatro superfícies da
invariante 5.

A rota da Evolution é `fetchInstances`, e **não** `connectionState/{instancia}` — conferido
subindo a imagem v2.3.7. Com a instância ainda inexistente, `connectionState` devolve `404` tanto
para a chave certa quanto para a errada: ele não separa credencial de nome, e sondar por ele
impediria salvar a configuração **antes** de criar a instância, que é a ordem em que a loja vai
fazer isso. `fetchInstances` responde `200` com a chave certa e `401` com a errada.

Consequência aceita: o nome da instância não é validado no momento de salvar. Quem o valida é a
[S-06](S-06-handoff-whatsapp.md), que é quem cria a instância.

A tela mostra ainda, somente leitura, o estado que a Evolution devolveu: `conectado`,
`desconectado` ou `não configurado`, com data da leitura. **O QR code não fica aqui** — ver §"Fora
do escopo".

**O teste faz o servidor buscar uma URL que uma pessoa digitou** (`evolution_url`, e `llm_url`
quando o provedor é `compativel`). É uma requisição de dentro da rede para um endereço arbitrário, e
ela nasce com as restrições em vez de ganhá-las depois de um incidente:

- só `http://` e `https://`;
- **redirecionamento não é seguido** — é assim que uma URL externa vira uma interna;
- tempo limite de 5 segundos;
- a resposta **não** volta para a tela: só se ela autenticou ou não. Devolver o corpo faria da tela
  um leitor de qualquer endereço que o servidor alcança.

O risco residual é aceito porque só o `dono` alcança a rota. Não é aceito em silêncio: está escrito
aqui, e é o que um dia justifica uma lista de destinos permitidos.

### 6. Precedência, cache e quando a troca passa a valer

```
configuracoes (banco)  →  .env  →  o padrão do preset
```

Num leitor só, `app/configuracao.py`. Cache em processo de **30 segundos**, invalidado na escrita do
próprio processo.

**Salvar na tela grava no banco, e só no banco. Nada, em nenhum caminho, escreve no `.env`** — ele é
gerado pelo [`gerar-segredos.sh`](../../scripts/gerar-segredos.sh) e sobrescrito no deploy seguinte,
então a troca feita pela tela sumiria sem aviso. O `.env` responde por uma chave em duas situações,
e só nelas: instalação nova, antes de alguém abrir a tela, e o CI dos evals da
[S-03 §8](S-03-agente-aurora.md), que injeta a chave do provedor por variável de ambiente sem passar
por navegador.

O provedor passa a ser montado **no início de cada turno**, a partir desse leitor, e não uma vez no
import como hoje ([`app/ia/provedor.py`](../../backend/app/ia/provedor.py)). Sem isso, salvar na tela
não teria efeito até alguém reiniciar a API — que é exatamente o passo que esta spec existe para
eliminar.

Isso move uma costura que a suíte inteira usa: hoje `turno.PROVEDOR` é um objeto de módulo, e o
`conftest` troca ele por um dublê com `monkeypatch.setattr(modulo_turno, "PROVEDOR", duble)` em
**todos** os testes, por fixture `autouse`. Quem implementar precisa manter um ponto único de
substituição — senão os 273 testes passam a falar com a rede de verdade e falham pelo motivo errado.
A `GET /health/ready` da [S-08 §7](S-08-observabilidade-e-custo.md) lê o mesmo objeto e vai junto.

Critério concreto: **a troca vale no próximo turno, em no máximo 30 segundos.**

Cada campo da tela diz de onde o valor em uso está vindo (`banco`, `.env` ou `padrão`). É o que
evita o incidente previsível: alguém edita o `.env`, nada muda, e ninguém entende por quê.

E quando o valor vem do banco **e** existe um diferente no `.env`, o campo avisa — porque o que
sobrou no arquivo é uma credencial antiga, viva, num arquivo do servidor. O runbook da
[S-10 §6](S-10-operacao.md) ganha a linha correspondente: **configurou pela tela, esvazie a linha no
`.env`.** Chave rotacionada que continua legível no disco é a rotação não tendo acontecido.

### 7. Rastro

Toda escrita grava na trilha ([ADR-006](../adr/ADR-006-observabilidade-e-teto-de-custo.md)):

```
tipo="evento", nome="configuracao_alterada",
dados={"chave": "llm_chave", "usuario_id": "..."}
```

**Nunca o valor** — nem o antigo, nem o novo, nem mascarado. A trilha é lida pelo Raí como
transcrição e por mim como trace: um segredo que passe por ela passa pelos dois, e sai do processo
nos dois.

A varredura da [S-09 §7](S-09-protecao-de-pii.md) passa a cobrir esta tela: trocar o WhatsApp da
Neuza é uma escrita de PII, e ela tem que continuar dando zero ocorrência.

### 8. O que esta tela nunca configura

Um formulário é a maneira mais fácil de contornar uma invariante, então a lista é parte da spec:

| Não entra | Por quê |
|---|---|
| Preço, catálogo, autonomia, disponibilidade | São do Postgres e vêm por tool — invariante 1, [ADR-003](../adr/ADR-003-numeros-nunca-saem-do-modelo.md) |
| Desconto, abatimento, condição especial | Não existe no sistema — invariante 2, [ADR-004](../adr/ADR-004-aprovacao-humana-no-irreversivel.md) |
| Teto de custo, prazo de reserva, validade da aprovação | São regra verificada por teste; campo para um número que um teste protege é o teste virando decoração |
| `EVSALES_PII_KEY`, `EVSALES_PII_PEPPER`, `EVSALES_JWT_SECRET` | A chave que cifra a tabela não pode morar na tabela que ela cifra ([S-10 §5](S-10-operacao.md)) |
| O prompt da Aurora | Mudança de prompt exige eval comparativo antes e depois (CLAUDE.md) |

### 9. A tela

`frontend/configuracoes.html`, servida em `GET /configuracoes`, no padrão das outras quatro: HTML
estático, `estilo.css`, `tema.js`, sem build. Três blocos, um `<form>` cada, um botão
**"Testar e salvar"** por bloco — salvar o WhatsApp não deve depender de a chave da LLM estar certa.

```
┌─ WhatsApp da loja ───────────────────────────────┐
│ Número       +55 83 99157-5299        (banco)    │
│ Evolution    https://…                (banco)    │
│ Instância    solevolt                 (banco)    │
│ Chave        ••••4f2a   [trocar]      (banco)    │
│ Estado       ● conectado · lido às 14:22         │
└──────────────────────────────────────────────────┘
┌─ Provedor da Aurora ─────────────────────────────┐
│ Provedor     [openrouter ▾]           (.env)     │
│ Modelo       …                        (.env)     │
│ Chave        ••••4f2a   [trocar]      (banco)    │
└──────────────────────────────────────────────────┘
┌─ Quem recebe aviso ──────────────────────────────┐
│ Neuza        (83) *****-5299                     │
│ Tarcísio     (83) *****-4471                     │
│ Jaqueline    —                                   │
└──────────────────────────────────────────────────┘
```

---

## Critérios de aceite

```gherkin
Cenário: só o dono alcança
  Dado que a Neuza está autenticada como gerente
  Quando ela chama GET /api/configuracoes
  Então recebe 404
  E o corpo é igual ao de uma rota inexistente

Cenário: o segredo nunca volta
  Dado que llm_chave está gravada
  Quando o Raí abre a tela
  Então a resposta traz "••••" e os quatro últimos caracteres
  E nenhuma rota da aplicação devolve o valor em claro

Cenário: campo vazio não apaga
  Dado que llm_chave está gravada
  Quando o Raí salva o bloco do provedor sem preencher a chave
  Então llm_chave continua com o valor anterior

Cenário: credencial inválida não é gravada
  Dado uma API-key que o provedor recusa
  Quando o Raí salva
  Então a resposta é 422 com a mensagem do provedor
  E configuracoes não tem linha para llm_chave
  E a mensagem do provedor não aparece em nenhum log

Cenário: o banco vence o .env
  Dado EVSALES_MODELO no .env
  E llm_modelo gravado com outro valor
  Quando um turno é montado
  Então o modelo usado é o do banco
  E a tela mostra "banco" ao lado do campo

Cenário: a troca vale sem reiniciar
  Dado um turno que já rodou com o modelo antigo
  Quando o Raí grava um modelo novo
  Então o turno seguinte usa o modelo novo, em no máximo 30 segundos

Cenário: provedor fora do ar não tranca a tela
  Dado que o provedor devolve 503
  Quando o Raí salva uma chave nova
  Então o valor é gravado
  E a tela avisa que não deu para confirmar a credencial agora

Cenário: credencial recusada é diferente de provedor caído
  Dado que o provedor devolve 401
  Quando o Raí salva
  Então nada é gravado

Cenário: o teste não segue redirecionamento
  Dado um evolution_url que responde 302 para um endereço interno
  Quando o Raí testa
  Então o servidor não busca o segundo endereço
  E a tela recebe só "não autenticou"

Cenário: o Raí recebe o escalonamento
  Dado um pedido sem decisão há 15 minutos
  E o telefone do Raí cadastrado na tela
  Então a notificação vai para ele

Cenário: trocar deixa rastro sem deixar o valor
  Quando o Raí grava llm_chave
  Então existe uma linha de trilha "configuracao_alterada" com a chave e o usuário
  E o valor não aparece na trilha, nem cifrado nem mascarado

Cenário: o número da loja obedece a regra do cliente
  Quando o Raí digita 558391575299
  Então a tela recusa com a mensagem da S-01
  E +5583991575299 é aceito

Cenário: a varredura continua zerada
  Dado que o Raí trocou o WhatsApp da Neuza
  Quando a varredura de PII da S-09 §7 roda
  Então ela encontra 0 ocorrências
```

## Fora do escopo

- **O QR code da instância.** Conectar o WhatsApp continua sendo ler o QR no manager da Evolution
  ([S-10 §2](S-10-operacao.md)). Embutir isso aqui seria fazer o EV-Sales intermediar uma sessão de
  WhatsApp, e a tela só mostra o estado que a Evolution reporta, com link para o manager.
- **Criar, desativar e trocar senha de usuário.** Já é da [S-11 §8](S-11-autenticacao-e-perfis.md).
- **Histórico de valores anteriores.** A trilha registra que mudou, quem e quando. Guardar o valor
  antigo seria guardar um segredo revogado — a pior linha possível para um dump conter.
- **Configuração por ambiente ou por loja.** Uma loja, um servidor ([S-10](S-10-operacao.md)).
- **Recarregar o `.env` sem reiniciar.** O `.env` é bootstrap; o que precisa mudar quente mudou de
  lugar nesta spec.
