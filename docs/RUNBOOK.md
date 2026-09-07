# Runbook — EV-Sales

Escrito para quem **não** construiu o sistema. Cada sintoma tem o comando exato: nenhuma
linha diz "verifique os logs".

Todos os comandos rodam na raiz do projeto, no servidor da loja, com o `.env` no lugar.

---

## Antes de tudo: a chave que não tem conserto

> **`EVSALES_PII_KEY` e `EVSALES_PII_PEPPER` não estão em nenhum backup, e é de propósito.**
> Elas vivem no gerenciador de senhas do Raí, fora do servidor. **Perder a chave é perder o
> nome e o telefone de todos os clientes, para sempre** — o backup do banco continua lá, e
> ilegível. Chave junto com banco cifrado é o mesmo que banco em claro.
>
> Se você está trocando de servidor: copie o `.env` **antes** de desligar o antigo.

---

## 1. A Aurora parou de responder no WhatsApp

**Causa provável:** a sessão da Evolution caiu. Acontece quando o WhatsApp desconecta o
aparelho pareado — o celular ficou dias sem internet, ou alguém deslogou pelo telefone.

```bash
./scripts/conferir-evolution.sh
```

Ele responde se o gateway está de pé, se a chave bate, em que estado a instância está e se
o webhook aponta para o EV-Sales.

- **"instância CONECTADA"** — a sessão está viva, o problema é outro. Vá para o item 3.
- **"aguardando leitura do QR"** ou **"está desconectada"** — precisa ler o QR de novo:

```bash
# 1. Abra o manager e entre com a chave que está em EVSALES_EVOLUTION_CHAVE no .env
xdg-open http://localhost:8080/manager

# 2. Instância `solevolt` → Connect → leia o QR no WhatsApp do número da loja
# 3. Confirme:
./scripts/conferir-evolution.sh
```

- **Nem o manager abre:**

```bash
docker compose ps evolution          # subiu?
docker compose logs --tail=50 evolution
docker compose restart evolution
```

O cliente **não perde a conversa**: as mensagens que chegaram estão em `mensagens`, e a
conversa continua pelo id (S-06 §5). Ele perde o tempo de espera.

---

## 2. "Atendimento indisponível" para todos os clientes

**Causa provável:** o teto de custo do mês foi atingido. A Aurora sai e a conversa vai para
a fila humana — é degradação escolhida, não falha ([S-08 §3](spec/S-08-observabilidade-e-custo.md)).

```bash
# O gasto do mês, na tela do Raí:
xdg-open http://localhost:8000/custo
```

O incidente `teto_atingido` está na lista da tela, e o WhatsApp do Raí recebeu o aviso
quando aconteceu.

**Decidir com o Raí se eleva.** O teto é código, não configuração — `TETO_MICRO_REAIS` em
`backend/app/observabilidade.py`. Elevar exige commit, revisão e deploy, e isso é de
propósito: um teto que se muda pela tela na hora do aperto não é um teto.

Se o painel mostra **R$ 0,00** e a Aurora parou de qualquer forma, o provedor configurado
não devolve custo faturado — procure o incidente `custo_nao_faturado` e leia o
[ADR-012](adr/ADR-012-provedor-configuravel.md).

---

## 3. A Aurora não responde, e a Evolution está de pé

**Causa provável:** a API caiu, ou o provedor de LLM está fora.

```bash
curl -s localhost:8000/health/ready    # 200 = banco e MinIO respondem
docker compose ps
docker compose logs --tail=50 api
docker compose restart api
```

> **Não existe container `worker`.** A [S-10 §1](spec/S-10-operacao.md) desenha um, e ele
> não foi construído: a rotina de 5 minutos (liberar reserva vencida, escalar aprovação,
> lembrete, cobrança de desfecho) é uma tarefa dentro do processo da API. Reiniciar a `api`
> reinicia a rotina. Se a spec mandar você reiniciar o worker, é a spec que está velha.

Provedor fora do ar entra na trilha como **evento** `provedor_indisponivel` (não é
incidente: é o mundo, não o sistema), e o cliente recebe a mensagem honesta do teto —
nunca uma resposta inventada:

```bash
docker compose exec -T postgres psql -U evsales -d evsales -c "
  SELECT criado_em, nome, conversa_id FROM trilha
   WHERE nome IN ('provedor_indisponivel', 'provedor_nao_configurado', 'resposta_vazia')
   ORDER BY criado_em DESC LIMIT 10;"
```

---

## 4. A Neuza não recebe a notificação de aprovação

**Causa provável:** telefone não cadastrado, ou a Evolution desconectada.

```bash
# 1. A sessão primeiro — sem ela nada sai:
./scripts/conferir-evolution.sh

# 2. Quem recebe aviso. A consulta pergunta se **existe** telefone, e não qual é:
docker compose exec -T postgres psql -U evsales -d evsales -c "
  SELECT nome, perfil, ativo, telefone_cifrado IS NOT NULL AS recebe_aviso
    FROM usuarios ORDER BY perfil, nome;"
```

Se a Neuza aparece com `recebe_aviso = f`, ela não cadastrou o telefone. A correção é na
tela do Raí:

```bash
xdg-open http://localhost:8000/configuracoes
```

> A [S-10 §6](spec/S-10-operacao.md) fala de uma variável `NEUZA_WHATSAPP`. **Ela não
> existe.** O telefone de quem recebe aviso mora em `usuarios.telefone_cifrado`, cifrado,
> desde a [S-12 §3](spec/S-12-configuracoes.md) — porque telefone de pessoa não é
> configuração de sistema.

Enquanto ninguém tem telefone, o pedido **não se perde**: ele fica na fila de
`/aprovacoes` e escala para o Raí em 15 minutos.

---

## 5. O cliente diz que reservou e o carro não está mais reservado

**Causa provável:** a reserva de 72 h venceu sem desfecho registrado. A rotina liberou o
carro e avisou a equipe de vendas — é o comportamento da
[S-05 §4](spec/S-05-reserva-de-chassi.md), não um erro.

```bash
# A história completa daquele chassi, com quem fez o quê e quando:
docker compose exec -T postgres psql -U evsales -d evsales -c "
  SELECT r.status, r.criada_em, r.expira_em, r.liberada_em, r.motivo_liberacao, r.renovacoes
    FROM reservas r
   WHERE r.chassi = 'CHASSI-AQUI'
   ORDER BY r.criada_em DESC;"
```

- `motivo_liberacao = 'prazo'` → venceu sozinha. **Decidir com a Neuza** se reserva de
  novo: o carro pode ter sido prometido a outra pessoa nesse meio-tempo.
- `motivo_liberacao = 'desistencia'` → um vendedor marcou `desistiu` no desfecho. Quem
  marcou está na trilha:

```bash
docker compose exec -T postgres psql -U evsales -d evsales -c "
  SELECT criado_em, dados FROM trilha WHERE nome = 'desfecho_registrado'
   ORDER BY criado_em DESC LIMIT 10;"
```

A reserva não se recria por comando: a Aurora não tem tool de cancelar nem de reservar sem
aprovação. Reservar de novo passa pela fila de `/aprovacoes`.

---

## 6. A Aurora citou um número errado

**Causa provável:** falha da verificação numérica ([S-03 §4](spec/S-03-agente-aurora.md)) —
ou, muito mais provável, ela **não** falhou: bloqueou a resposta e mandou a conversa para
humano, e é isso que o cliente relatou como "ela travou".

```bash
# Os incidentes de número, com a etapa e o trecho divergente:
docker compose exec -T postgres psql -U evsales -d evsales -c "
  SELECT criado_em, conversa_id, dados FROM incidentes
   WHERE tipo = 'numero_divergente' ORDER BY criado_em DESC LIMIT 10;"

# O span da verificação daquela conversa: o que foi extraído, o que era permitido:
docker compose exec -T postgres psql -U evsales -d evsales -c "
  SELECT criado_em, dados FROM trilha
   WHERE conversa_id = 'ID-DA-CONVERSA' AND tipo = 'verificacao'
   ORDER BY criado_em;"
```

A trilha guarda **argumentos e retorno inteiros** de cada tool, então dá para provar de
onde veio cada número. A tela do Raí lê a mesma trilha como transcrição:

```bash
xdg-open http://localhost:8000/atendimentos
```

**Se o número saiu para o cliente**, é defeito de verdade e é grave: abra issue com o id da
conversa, o texto que saiu e o span da verificação. Não conserte o eval para o caso passar —
o portão de CI existe para essa resposta reprovar ([CLAUDE.md](../CLAUDE.md)).

---

## Backup

```bash
./scripts/backup.sh          # tudo; diário às 03h pelo cron
./scripts/conferir-backup.sh # restaura o dump mais novo num banco descartável
```

No cron, com o destino num disco externo montado:

```cron
0 3 * * * cd /opt/ev-sales && EVSALES_BACKUP_DIR=/mnt/backup ./scripts/backup.sh >> /var/log/ev-sales-backup.log 2>&1
```

O `EVSALES_BACKUP_DIR` (padrão `./backups`) recebe:

| Pasta | O quê | Retenção |
|---|---|---|
| `postgres/evsales-*.sql.gz` | os dados da loja | 30 dias |
| `postgres/evolution-*.sql.gz` | a instância do WhatsApp | 7 dias |
| `evolution/instancias-*.tgz` | o volume da sessão | 7 dias |
| `documentos/documentos-*.tgz` | os Espelhos emitidos | 30 dias |
| `fotos/fotos-*.tgz` | as fotos do catálogo (só domingo) | 4 semanas |

**A cópia externa é sua.** O script escreve numa pasta; levar essa pasta para fora do
servidor — `rclone`, disco removível, o que a loja usar — é uma linha no cron de quem
opera, e não uma dependência do projeto.

### Testar a restauração — uma vez por mês

`./scripts/conferir-backup.sh` restaura o dump mais recente num banco `evsales_restauracao`,
confere que o `psql` aceitou o arquivo inteiro, que a versão do schema é a que o código
espera, e lista as tabelas do domínio. Ao fim, derruba o banco de teste.

Ele **nunca** toca no `evsales`. Backup não testado é fé.

Se ele reclamar de versão de schema, o dump é de antes de uma migration:
`alembic upgrade head` depois de restaurar resolve — e aí teste de novo.

### Restaurar de verdade

```bash
# 1. Pare a API para ninguém escrever durante a restauração:
docker compose stop api

# 2. Confira que a chave de PII em uso é a MESMA de quando o backup foi feito.
#    Se não for, os nomes e telefones não vão decifrar. Isto não tem volta.

# 3. Recrie o banco e restaure:
docker compose exec -T postgres psql -U evsales -d postgres \
  -c "DROP DATABASE IF EXISTS evsales" -c "CREATE DATABASE evsales OWNER evsales"
gunzip -c /mnt/backup/postgres/evsales-AAAA-MM-DD.sql.gz \
  | docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U evsales -d evsales

# 4. Os Espelhos:
tar xzf /mnt/backup/documentos/documentos-AAAA-MM-DD.tgz -C /tmp
# suba os arquivos de /tmp/documentos para o bucket pelo console do MinIO:
xdg-open http://localhost:9001

# 5. A sessão do WhatsApp: restaure o volume, ou leia o QR de novo (item 1).

# 6. Suba tudo e confira:
docker compose up -d
curl -s localhost:8000/health/ready
```

**O que uma restauração perde:** as mensagens que chegaram entre o último backup e a
queda. Se a Evolution ainda tem a sessão, os clientes vão reescrever; a conversa continua
pelo telefone deles ([S-06 §4](spec/S-06-handoff-whatsapp.md)).
