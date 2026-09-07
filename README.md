---
projeto: EV-Sales — Agente de Vendas de Ponta a Ponta
autor: Vlademir Souza
usuario: vladesouza7
dominio: Concessionária multimarca de veículos elétricos
repositorio: https://github.com/vladesouza7/desafio-jornada/tree/main/desafio-vendas/projetos/vladesouza7/EV-Sales
linkedin: https://www.linkedin.com/in/vlademir-souza-ads
portfólio: https://vladetec.github.io/portifolio-blog-posts/templates/index.html
plataforma: https://suajornadadedados.curseduca.pro/m/community/posts/a773a771-9319-4f51-8340-2358d8d9832d
video: https://vladetec.github.io/portifolio-blog-posts/templates/index.html
---

# EV-Sales — a Aurora, consultora da Sol & Volt

A **Sol & Volt** é uma concessionária multimarca de carros elétricos em Tambaú, João Pessoa. O
**Raí** vendeu a loja de combustão que era do pai dele, em 2022, e apostou tudo em elétrico. Ele
recebe cerca de 280 conversas por mês, tem dois vendedores, e **41% dessas conversas morrem sem
resposta no mesmo dia**.

A **Aurora** atende do "quero um elétrico até 150 mil" até o test drive marcado — passando pela
condição aprovada pela gerente e pela reserva do chassi. Ela nunca diz um preço que não leu no
banco, e para de vez antes de qualquer coisa que não tenha volta.

E ela **não vende o carro**. Ninguém compra um elétrico de R$ 250 mil sem sentar dentro dele: nota
fiscal, faturamento contra a montadora e crédito aprovado por banco já acontecem na loja, por
obrigação legal. O fim da jornada digital é colocar um cliente qualificado dentro do carro certo,
com um vendedor que já sabe o que ele precisa.

## O detalhe que define a arquitetura

Concessionária não vende SKU com quantidade. Vende **unidade com chassi**. Não existe "3 Seal em
estoque" — existe o Seal branco `…4471` e o Seal cinza `…9902`. E metade do estoque é seminovo
premium, onde não existe "outro igual" nem na teoria: aquele Taycan tem aquela quilometragem e
aquele preço.

O medo que o Raí repetiu mais vezes:

> "Carro eu tenho um de cada. Se esse negócio prometer o mesmo Seal branco pra duas pessoas,
> alguém vai ter que ligar pra uma delas e desmarcar. E esse alguém sou eu."

Isso torna a reserva irreversível de verdade, e faz a operação crítica do sistema ser uma corrida
entre dois clientes pelo mesmo carro — um pelo site, outro pelo WhatsApp, no mesmo segundo.

## Arquitetura

Um agente, tools filtradas por etapa da conversa, e uma pausa antes do irreversível:

```
 landing ──▶ chat (SSE) ──▶ AURORA ──⏸── NEUZA aprova ──▶ reserva ──▶ test drive
    │                          │                            │              │
    │                     tools por etapa            UPDATE … WHERE        │
    │                     (a lista muda,             status='disponivel'   │
    │                      não o prompt)             ← a corrida acaba aqui│
    │                                                                      │
    └──▶ wa.me ──▶ WhatsApp ──▶ mesma conversa, mesmo id, outro canal      │
                                                                           │
 ═════════════ fim do EV-Sales ════════════════════════════════════════════╪══════
                                                                           │
              a venda é PRESENCIAL, no sistema que a loja já tem     desfecho ◀┘
              (financiamento, faturamento, NF-e, documentação)       1 toque
```

Na etapa de qualificação, **não há tool de escrita registrada** — não é uma proibição no prompt, é
uma lista que não contém a função. Na etapa de espera pela Neuza, a lista está vazia. E a pausa não
é mecanismo: é uma linha no Postgres, então reiniciar o container no meio não tem efeito.

## Stack

| Camada | Escolha | Por quê |
|---|---|---|
| Orquestração | **Nenhuma** — loop de tools + estado em tabelas | O Raí precisa ler onde a conversa parou, com SQL. Checkpoint serializado guardaria o estado uma segunda vez, e estado duplicado diverge |
| Dados | Postgres + pgvector | Fonte da verdade de preço *e* estado do agente. ~2.800 chunks não pagam um banco vetorial dedicado |
| LLM | OpenRouter atrás de interface própria | Custo real por conversa vem do faturamento; trocar de modelo é uma variável de ambiente |
| WhatsApp | Evolution API, **só respondendo** | O cliente inicia por `wa.me`. Disparo ativo arrisca o número da vitrine |
| Observabilidade | Langfuse self-hosted | Duas leituras da mesma origem: transcrição para o Raí, trace para mim |
| API / Front | FastAPI, com o front em HTML/CSS/JS **sem build** | Cinco telas estáticas não pagam um pipeline de build, e a equipe da Sol & Volt abre o arquivo e lê. Decisão ainda sem ADR — ver [ARQUITETURA](docs/ARQUITETURA.md#onde-o-código-está-hoje) |

Cinco containers. **Sem Qdrant, sem MinIO, sem Prometheus/Grafana/Loki** — cada ausência tem ADR.

## Harness

Claude Code, com o harness versionado junto do código. `CLAUDE.md` enxuto, com **cinco invariantes**
que o agente não pode violar sem parar e perguntar, e uma lista explícita do que **não** fica na mão
dele: migrations que tocam estoque e aprovação, a lista de tools por etapa, os prompts, o módulo de
PII, e os ADRs — que são registro histórico, nunca editados.

O comando que mais mudou o resultado foi o `/verificar-spec`: uma sessão limpa, que nunca viu a
implementação, lê a spec e emite veredito — e **não tem permissão de corrigir** o que encontra.

Também está escrito lá, em letras grandes: nunca marcar um eval como `skip` para o build passar.

## As três decisões mais difíceis

**1. Recusei o MongoDB, que eu mesmo tinha proposto.** Ele modelaria melhor um catálogo multimarca
heterogêneo. Perdeu por uma razão só: o pior erro deste domínio é uma condição de corrida, e no
Postgres a proteção é uma constraint que recusa a segunda gravação **mesmo se o meu código estiver
errado** — inclusive se um agente introduzir um caminho novo que eu revisei mal.

**2. Não usei framework de orquestração.** O `interrupt` com checkpointer é uma primitiva melhor do
que qualquer coisa que eu escreva à mão. Recusei porque ele guardaria o estado uma segunda vez, em
formato serializado, e o Raí precisa **ler** onde a conversa parou com um `SELECT`. Fica o gatilho
escrito: um segundo ponto de aprovação com ramificação reabre a decisão.

**3. Não simulei a venda.** O caminho fácil para "cobrir tudo" era emitir uma NF-e simulada e
chamar aquilo de fim da jornada. Recusei porque é fachada: nenhum risco real do domínio é
exercitado ali, e todo o valor de engenharia — preço fora do modelo, corrida por chassi único,
aprovação humana, PII — já está antes desse ponto. No lugar do PDF de venda entrou o artefato que
o vendedor realmente usa: o **dossiê do atendimento**, que o Tarcísio abre no celular antes do
test drive ([ADR-011](docs/adr/ADR-011-jornada-digital-termina-no-test-drive.md)).

*Menção honrosa:* mantive o cadastro antes do chat **contra a minha recomendação**, porque o Raí
tinha um argumento melhor que o meu para a realidade dele. O
[ADR-010](docs/adr/ADR-010-cadastro-antes-do-chat.md) registra a minha objeção e a métrica que
decide a discussão quando houver dado.

## O que eu aprendi

Que arquitetura grande é fácil de desenhar e difícil de defender. O meu primeiro rascunho tinha nove
serviços, e eu não conseguia justificar metade deles sem dizer "para escalar" — que é o que a gente
fala quando não tem número. Cortei quatro, e cada corte virou um ADR com gatilho de revisão escrito
com número, não com adjetivo.

E aprendi que **escrever o gatilho não basta: tem que rodar quando ele dispara.** Coloquei "reabrir
se o catálogo passar de 60 modelos" no ADR do pgvector. O catálogo real chegou a 58, e eu quase subi
um Qdrant — até olhar a métrica ao lado: o corpus estava em 14% do limite. Uma gritando, a outra
dormindo, medindo o mesmo sistema. Não era sinal de migrar, era prova de que **contagem de modelos
era um proxy ruim** para custo de busca vetorial. Corrigi a métrica em vez de obedecê-la, e registrei
o porquê. Obedecer teria custado um serviço inteiro operando a 14% do que eu já tinha.

E que risco sem verificação automatizada é desejo, não requisito. Enquanto os evals não estavam no
CI bloqueando merge, os meus próprios documentos envelheciam em silêncio dizendo que estava tudo
mitigado.

---

## Documentação

**Estado da implementação:** o quadro por spec fica em
[docs/spec/README.md](docs/spec/README.md#as-specs). Em resumo: a jornada inteira está de pé —
landing, chat, a Aurora com verificação numérica, a fila da Neuza, a reserva de chassi, o handoff
para o WhatsApp, o test drive com desfecho, o trace e os cinco portões de CI. O que falta são
bordas nomeadas em cada spec: o Langfuse como segunda leitura, a retenção de PII, o dossiê do
vendedor e as duas tools que a Aurora ainda não tem.

| | |
|---|---|
| [CASE](docs/CASE.md) | O negócio, o Raí, a Neuza, as personas e a jornada |
| [PRD](docs/PRD.md) | Problema, escopo, o que fica de fora e como o sucesso é medido |
| [ARQUITETURA](docs/ARQUITETURA.md) | Um agente, onde ficam os dados, onde entra o humano, o que acontece quando falha |
| [RUNBOOK](docs/RUNBOOK.md) | Os seis incidentes que vão acontecer, com o comando exato de cada um |
| [ADRs](docs/adr/) | 14 decisões, cada uma com a alternativa descartada |
| [SPECs](docs/spec/) | 12 specs com critérios de aceite executáveis |
| [CLAUDE.md](CLAUDE.md) | O harness: invariantes, limites do agente e como eu reviso |

## Backup, e a chave que não tem conserto

```bash
./scripts/backup.sh          # diário às 03h pelo cron; a linha está no RUNBOOK
./scripts/conferir-backup.sh # restaura o dump mais novo num banco descartável, uma vez por mês
```

> **`EVSALES_PII_KEY` e `EVSALES_PII_PEPPER` não entram em backup nenhum, de propósito.**
> Chave junto com banco cifrado é o mesmo que banco em claro. Elas ficam no gerenciador de
> senhas do Raí, **fora do servidor** — e **perder a chave é perder o nome e o telefone de
> todos os clientes, para sempre**: o dump continua lá, e ilegível. É o erro que não tem
> conserto, e é por isso que ele está em negrito aqui e no
> [RUNBOOK](docs/RUNBOOK.md#antes-de-tudo-a-chave-que-não-tem-conserto).

Backup não testado é fé: o `conferir-backup.sh` restaura num banco `evsales_restauracao`,
confere que o `psql` aceitou o arquivo inteiro e que a versão do schema é a que o código
espera, e derruba o banco no fim. Ele nunca toca no `evsales`.
