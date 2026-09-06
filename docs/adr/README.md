# ADRs — EV-Sales

Registro das decisões de arquitetura do EV-Sales. Cada uma traz o contexto que a exigiu, as
alternativas que foram descartadas e as consequências aceitas — inclusive as ruins.

> Decisão sem alternativa descartada não é decisão, é acaso.

| # | Decisão | Nasce de | Alternativa mais difícil de recusar |
|---|---|---|---|
| [001](ADR-001-postgres-fonte-da-verdade.md) | Postgres como fonte da verdade de estoque e preço | Fala 2 do Raí — "carro eu tenho um de cada" | MongoDB |
| [002](ADR-002-pgvector-em-vez-de-qdrant.md) | pgvector no v1, Qdrant só quando o catálogo justificar | 58 modelos, ~2.800 chunks — reavaliado, mantido | Qdrant desde o v1 |
| [003](ADR-003-numeros-nunca-saem-do-modelo.md) | Nenhum número sai do modelo | Fala 1 — "ele descobre no test drive" | Verificar só na proposta |
| [004](ADR-004-aprovacao-humana-no-irreversivel.md) | Aprovação humana antes do irreversível; desconto inexistente | Fala 3 — "não sai sem a Neuza ver" | Aprovação só acima de um valor |
| [005](ADR-005-handoff-whatsapp-por-wa-me.md) | O cliente inicia o WhatsApp, não a loja | Risco de ban no número comercial | Disparo ativo pela Evolution |
| [006](ADR-006-observabilidade-e-teto-de-custo.md) | Trace legível e teto de custo desde o commit 1 | Falas 4 e 6 — auditar e não estourar | Observabilidade depois do agente |
| [007](ADR-007-pii-cifrada-e-mascarada.md) | PII cifrada em repouso, mascarada na origem | Fala 5 — "telefone é o meu ativo" | Tokenizar e nunca guardar o número |
| [008](ADR-008-openrouter-como-provedor.md) | OpenRouter atrás de uma interface própria | Custo real por conversa + não acoplar | Ollama local |
| [009](ADR-009-sem-framework-de-orquestracao.md) | Sem framework: loop de tools + estado no Postgres | Um ponto de pausa, estado que o Raí lê | LangGraph com checkpointer |
| [010](ADR-010-cadastro-antes-do-chat.md) | Nome e telefone antes do chat, com saída pelo lado | Decisão de produto do Raí | Chat aberto, telefone no meio |
| [011](ADR-011-jornada-digital-termina-no-test-drive.md) | A jornada digital termina no test drive; a venda é presencial | NF, faturamento e crédito já existem no DMS | Simular a venda completa com NF-e |
| [012](ADR-012-provedor-configuravel.md) | Provedor de LLM por configuração; a trilha diz se o custo é faturado | Não depender de uma conta em intermediário | Uma classe por fabricante |
| [013](ADR-013-minio-para-arquivo-gerado.md) | MinIO volta, para arquivo gerado e foto que alguém sobe | Espelho em PDF e upload de foto | Guardar os arquivos no Postgres |
| [014](ADR-014-configuracao-operacional-no-banco.md) | Credencial e número saem do `.env` para o banco, cifrados | A S-06 e uma chave que vence no sábado | Guardar em claro, protegido pelo perfil |

## As três que mais custaram

**1. Descartar MongoDB ([001](ADR-001-postgres-fonte-da-verdade.md)).** Eu mesmo tinha levantado o
Mongo, e ele modelaria melhor um catálogo multimarca heterogêneo. Perdeu por uma razão só: o pior
erro possível neste domínio é uma condição de corrida, e no Postgres a proteção contra ela é uma
constraint que recusa a segunda gravação **mesmo se o meu código estiver errado**.

**2. Não usar framework de orquestração ([009](ADR-009-sem-framework-de-orquestracao.md)).** O
`interrupt` do LangGraph é uma primitiva melhor do que qualquer coisa que eu escreva. Recusei
porque ele guardaria o estado uma segunda vez, em formato serializado — e o Raí precisa **ler**
onde a conversa parou, com SQL, sem biblioteca. Estado duplicado é estado que diverge.

**3. Não simular a venda ([011](ADR-011-jornada-digital-termina-no-test-drive.md)).** O caminho para
"cobrir tudo o que o enunciado cita" era emitir uma NF-e simulada e chamar aquilo de fim da jornada.
Recusei porque seria fachada: nenhum risco real do domínio é exercitado ali, e o enunciado proíbe
documento com validade real justamente porque simulação fiel de nota fiscal é um arquivo perigoso.
No domínio de veículos, o fim da jornada digital **é** o test drive — e assumir isso concentrou o
esforço onde estão os riscos de verdade.

Menção honrosa: **manter o cadastro antes do chat ([010](ADR-010-cadastro-antes-do-chat.md))**. Eu
recomendei o contrário e o cliente manteve a decisão dele, com um argumento melhor que o meu para a
realidade da loja. O ADR registra a minha objeção, as mitigações que negociamos e **a métrica que
decide a discussão** quando houver dado. Documentar decisão que não foi minha, com a hipótese
contrária preservada, é parte do trabalho.

## Padrão

Cada ADR segue: **Contexto** (o que exigiu a decisão, ancorado no case) → **Decisão** (o que vale,
concreto) → **Alternativas consideradas** (cada uma com o motivo real da recusa) → **Consequências**
(aceitas e ganhas).

Quando uma decisão tem gatilho de revisão, ele está escrito com número — não "se crescer", mas
"quando passar de 60 modelos".
