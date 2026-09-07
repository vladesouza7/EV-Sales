# S-10 — Operação: subir, semear, fazer backup

**Depende de:** todas
**Nasce de:** *"Quero que a minha equipe, e não só você, consiga colocar isso para rodar"*
**Estado:** ◐ parcial — sobe, semeia, faz backup, restaura e reprova no CI; o compose tem 4
dos 9 containers desenhados, e o seed é o mínimo.

| § | O quê | Estado |
|---|---|---|
| §1 | Os containers | ◐ `postgres`, `minio`, `evolution` e `api`. Faltam `nginx`, `frontend`, `redis`, `worker` e `langfuse` — e a rotina que seria do `worker` é uma tarefa no processo da API, escrito no [RUNBOOK](../RUNBOOK.md#3-a-aurora-não-responde-e-a-evolution-está-de-pé) para ninguém procurar container que não existe |
| §2 | Subir do zero | ✔ `gerar-segredos.sh` e `subir-dev.sh` |
| §3 | Migrations | ✔ `conferir-migrations.sh`, e o CI roda o mesmo comando |
| §4 | Seed | ◐ 7 unidades e 2 vendedores, o que a S-01 precisa; os 58 modelos e a base de conhecimento não |
| §5 | Backup | ✔ `backup.sh` (diário) e `conferir-backup.sh` (restauração testada) |
| §6 | Runbook | ✔ [docs/RUNBOOK.md](../RUNBOOK.md) |
| §7 | CI | ✔ os cinco portões; falta o segredo `EVSALES_LLM_API_KEY` no repositório para os três de eval ficarem verdes |

---

## Objetivo

Que alguém que não sou eu suba o EV-Sales do zero, sem me perguntar nada, seguindo um README.

## Comportamento

### 1. Os cinco containers

```
                          INTERNET
                             │
                    ┌────────┴────────┐
                    │  nginx (prod)   │
                    └────────┬────────┘
             ┌───────────────┼───────────────┐
             ▼               ▼               ▼
        ┌─────────┐    ┌──────────┐   ┌──────────────┐
        │ frontend│    │   api    │   │  evolution   │
        │React+Vite│   │ FastAPI  │◀──│  (WhatsApp)  │
        └─────────┘    └────┬─────┘   └──────────────┘
                            │  ▲
                       ┌────┘  └──────┐
                       ▼              ▼
                 ┌──────────┐   ┌──────────┐
                 │ postgres │   │  redis   │
                 │+pgvector │   │ fila e   │
                 │          │   │ contador │
                 └──────────┘   └────┬─────┘
                       ▲             │
                       └──────┬──────┘
                              ▼
                        ┌──────────┐
                        │  worker  │
                        └──────────┘
                              │
                              ▼
                     langfuse (observabilidade)
```

| Serviço | Imagem | Papel |
|---|---|---|
| `postgres` | `postgres:17` + pgvector | Fonte da verdade ([ADR-001](../adr/ADR-001-postgres-fonte-da-verdade.md), [ADR-002](../adr/ADR-002-pgvector-em-vez-de-qdrant.md)) |
| `redis` | `redis:8-alpine` | Fila, lock de conversa, contador de custo |
| `api` | build local | FastAPI: chat, webhook, aprovação, telas |
| `worker` | mesma imagem da api | Processa turnos e envios |
| `evolution` | `evoapicloud/evolution-api` | Gateway WhatsApp ([ADR-005](../adr/ADR-005-handoff-whatsapp-por-wa-me.md)) |
| `langfuse` | oficial | Trace e custo ([ADR-006](../adr/ADR-006-observabilidade-e-teto-de-custo.md)) |
| `frontend` | build local | Landing, chat, telas internas |
| `nginx` | `nginx:alpine` | Só no perfil `prod` |
| `minio` | `minio/minio` | Fotos de unidade e o PDF do Espelho ([ADR-013](../adr/ADR-013-minio-para-arquivo-gerado.md)) |

**Sem Qdrant, sem Prometheus, sem Grafana, sem Loki** — cada ausência tem ADR.

O MinIO estava nesta lista e **voltou** ([ADR-013](../adr/ADR-013-minio-para-arquivo-gerado.md)).
A frase que o cortava — "são ~20 arquivos, object store é overkill" — valia para foto estática
pré-carregada no volume, e não cobre os dois casos reais: foto que alguém **sobe** depois do
deploy, e o PDF do Espelho, que é **gerado** em runtime e carrega PII.

### 2. Subir do zero

```bash
git clone <repo> && cd ev-sales
cp .env.example .env
./scripts/gerar-segredos.sh     # gera PII_KEY, PII_PEPPER, JWT_SECRET no .env
# editar .env: OPENROUTER_API_KEY e EVOLUTION_NUMERO
docker compose up -d
./scripts/seed.sh               # catálogo, unidades, vendedores, conhecimento
```

Depois: conectar o WhatsApp lendo o QR code em `http://localhost:8080/manager`.

Pronto em **menos de 10 minutos** numa máquina limpa. É um critério de aceite, testado numa VM
zerada antes de qualquer release.

`scripts/gerar-segredos.sh` existe para que ninguém precise saber gerar chave AES — e para que
ninguém use a chave do `.env.example`, que é propositalmente inválida.

### 3. Migrations

Alembic. `docker compose up` roda `alembic upgrade head` antes de subir a API.

Toda migration precisa de `downgrade` que funcione, e é testada nos dois sentidos no CI. Migration
que altera constraint de estoque ou de reserva exige revisão humana explícita no PR — é o schema que
protege o Raí ([ADR-001](../adr/ADR-001-postgres-fonte-da-verdade.md)).

### 4. Seed

`scripts/seed.sh` popula:

- **~58 modelos de 18 marcas**, a partir de
  [MARCAS-E-MODELOS-BR.md](../pesquisa/MARCAS-E-MODELOS-BR.md), com autonomia, `autonomia_fonte`,
  bateria, potência e categoria vindos de
  [CATALOGO-E-OBJECOES.md](../pesquisa/CATALOGO-E-OBJECOES.md). **Modelo sem número confirmado entra
  com `NULL`, nunca com estimativa** ([ADR-003](../adr/ADR-003-numeros-nunca-saem-do-modelo.md));
- **17 unidades** com chassi, cor, `condicao`, km, preço e status — misturando novos e seminovos
  premium, para que o teste de concorrência e a busca exerçam os dois casos;
- **3 usuários**: Raí (`dono`), Neuza (`gerente`), Tarcísio e Jaqueline (`vendedor`);
- **Base de conhecimento**: 4 objeções clássicas (autonomia, tempo de carga, vida útil da bateria,
  custo de manutenção), garantia, carregamento e a rota João Pessoa–Recife, com embeddings.

Idempotente: rodar duas vezes não duplica.

Os chassis do seed são fictícios e marcados como tal.

### 5. Backup

| O quê | Frequência | Onde | Retenção |
|---|---|---|---|
| Postgres (`pg_dump`) | diário, 03h | volume + cópia externa | 30 dias |
| `EVSALES_PII_KEY` e `PEPPER` | manual, na criação | **fora do servidor** — gerenciador de senhas do Raí | — |
| Sessão da Evolution | diário | volume | 7 dias |
| MinIO — `fotos/` | semanal | volume | 4 semanas |
| MinIO — `documentos/` | diário, 03h | volume + cópia externa | 30 dias, e a retenção da [S-09 §6](S-09-protecao-de-pii.md) apaga junto com o lead |

> **Perder a chave de PII é perder o acesso a todos os nomes e telefones.** O backup do banco sem a
> chave é inútil. Está em negrito no README e no runbook porque é o erro que não tem conserto.

Restauração é testada uma vez por mês, num ambiente separado. Backup não testado é fé.

**Como ficou, e onde diverge do quadro acima:**

- `scripts/backup.sh`, diário pelo cron. A linha do cron está no
  [RUNBOOK](../RUNBOOK.md#backup), e o destino é `EVSALES_BACKUP_DIR` — que pode ser um
  disco externo montado. A "cópia externa" da tabela é uma linha de quem opera (`rclone`,
  disco removível): o script escreve numa pasta e não sabe fazer upload, porque credencial
  de nuvem no servidor da loja é uma superfície nova para guardar um backup.
- **A sessão da Evolution é backupeada em dois lugares, e a tabela só previa um.** Com
  `DATABASE_ENABLED=true` a instância vive no Postgres, não só no volume: o script leva o
  dump do banco `evolution` **e** o volume, os dois com 7 dias. Backup só do volume não
  dispensaria ler o QR de novo.
- `documentos/` e `fotos/` saem como **cópia datada**, não espelho. Espelho com `--remove`
  apagaria do backup o que alguém apagou por acidente; espelho sem `--remove` guardaria
  para sempre o Espelho de um lead que a retenção da [S-09 §6](S-09-protecao-de-pii.md)
  mandou apagar. Cópia datada expira sozinha, e é o que faz as duas coisas certas.
- `scripts/conferir-backup.sh` é a restauração testada: banco descartável, `ON_ERROR_STOP`
  ligado, e ele **compara a versão do Alembic no dump com a que o código espera** — um
  backup de schema antigo restaura sem erro e depois a API não sobe.

### 6. Runbook — os incidentes que vão acontecer

`docs/RUNBOOK.md`, escrito para quem não construiu o sistema:

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| Aurora parou de responder no WhatsApp | Sessão da Evolution caiu | Reconectar pelo manager, ler o QR |
| "Atendimento indisponível" para todos | Teto de custo atingido | Ver `/custo`; decidir com o Raí se eleva |
| Fila do worker crescendo | Worker caiu ou LLM lento | `docker compose restart worker`; ver `/health/ready` |
| Neuza não recebe notificação | Evolution desconectada ou número errado | Verificar sessão e `NEUZA_WHATSAPP` |
| Cliente diz que reservou e o carro sumiu | Reserva de 72h expirou | Consultar auditoria; decidir com a Neuza |
| Aurora citou número errado | Falha da verificação | Abrir o trace, achar o span de verificação, abrir issue com o caso |

Cada linha tem o comando exato. Sem "verifique os logs".

**Como ficou:** [docs/RUNBOOK.md](../RUNBOOK.md), com as seis linhas e três correções de
rota que a tabela acima já não descrevia:

- **não existe container `worker`** — a rotina de 5 minutos é uma tarefa no processo da
  API, e o runbook manda reiniciar a `api`. Mandar alguém reiniciar um container que não
  existe é o pior tipo de runbook: o que dá confiança errada às 23h;
- **`NEUZA_WHATSAPP` não existe** — o telefone de quem recebe aviso mora em
  `usuarios.telefone_cifrado` desde a [S-12 §3](S-12-configuracoes.md), cifrado. O comando
  do runbook pergunta **se existe** telefone, nunca qual é;
- **elevar o teto de custo é commit, não tela.** A tabela dizia "decidir com o Raí se
  eleva", e o runbook diz onde: `TETO_MICRO_REAIS`, com revisão e deploy. Teto que se muda
  pela tela na hora do aperto não é teto.

### 7. CI — o que bloqueia merge

| Verificação | Bloqueia? |
|---|---|
| Lint e formatação (ruff, eslint) | sim |
| Tipos (mypy, tsc) | sim |
| Testes unitários | sim |
| **Teste de concorrência de reserva** ([S-05](S-05-reserva-de-chassi.md)) | **sim** |
| **Teste de PII nos logs** ([S-09](S-09-protecao-de-pii.md)) | **sim** |
| **Eval de preço e estoque** ([S-03](S-03-agente-aurora.md)) | **sim** |
| **Eval de autonomia e fonte** ([S-03](S-03-agente-aurora.md)) | **sim** |
| **Eval de injection** ([S-03](S-03-agente-aurora.md)) | **sim** |
| Migration com `downgrade` funcional | sim |
| Cobertura ≥ 70% no core | sim |
| Evals de qualificação e objeção | não — reporta, não bloqueia |

`main` protegida: sem push direto, PR obrigatório, CI verde para merge.

Os cinco portões em negrito são os que traduzem os medos do Raí em algo que reprova build. Sem eles,
os ADRs envelhecem em silêncio dizendo que está tudo mitigado.

---

## Critérios de aceite

```gherkin
Cenário: sobe do zero em menos de 10 minutos
  Dado uma máquina limpa com Docker
  Quando sigo o README do começo ao fim
  Então todos os serviços ficam saudáveis
  E a landing responde em localhost
  E o catálogo mostra as 17 unidades do seed

Cenário: seed é idempotente
  Quando rodo o seed duas vezes
  Então continuam existindo 17 unidades

Cenário: nenhum segredo real no repositório
  Quando varro o repositório em busca de segredos
  Então .env não está versionado
  E .env.example não tem nenhuma chave válida

Cenário: migration reversível
  Quando aplico e reverto todas as migrations
  Então o schema volta ao estado anterior sem erro

Cenário: os cinco portões reprovam o build
  Dado um PR que quebra o teste de concorrência de reserva
  Então o CI reprova
  E o merge fica bloqueado

Cenário: restauração funciona
  Dado um backup do Postgres e a chave de PII
  Quando restauro em ambiente limpo
  Então os leads são legíveis e os telefones decifram
```

## Fora do escopo

- Kubernetes, Terraform, orquestração além do Compose.
- Alta disponibilidade e réplica de leitura — uma loja, um servidor.
- Deploy automático em produção — o v1 sobe manualmente, com o Raí sabendo.
- Multi-ambiente além de `dev` e `prod`.
