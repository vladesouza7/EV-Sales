# ADR-009 — Sem framework de orquestração: loop de tools + máquina de estados no Postgres

**Status:** aceito
**Data:** 2026-09-03

---

## Contexto

O EV-Sales tem um agente (Aurora), um conjunto pequeno de tools de leitura, e **um único ponto de
pausa** — a aprovação da Neuza ([ADR-004](ADR-004-aprovacao-humana-no-irreversivel.md)).

A pausa tem um requisito duro que costuma justificar um framework: ela precisa **sobreviver a
tudo**. A conversa começa no chat web às 22h de sábado, para esperando aprovação, o container
reinicia no deploy de domingo, a Neuza aprova pelo celular na segunda de manhã e a conversa
continua — **no WhatsApp, não mais no site**. É a mesma conversa em dois canais, com dias de
intervalo e um restart no meio.

Esse é exatamente o cenário que vende LangGraph: `interrupt()` com estado persistido em
checkpointer é feito para isso.

## Decisão

**Sem framework de orquestração no v1.** O agente é um loop explícito de tool calling, e o estado
da conversa é uma máquina de estados materializada em tabelas do Postgres.

```python
# em cada turno, o estado é RECONSTRUÍDO do Postgres — nunca mantido em memória
estado = carregar_estado(conversa_id)  # etapa, qualificação, chassi em foco
historico = carregar_mensagens(conversa_id)  # a conversa inteira, dos dois canais

while True:
    resposta = llm.chamar(prompt(estado), historico, tools=tools_da_etapa(estado))
    if not resposta.tool_calls:
        break
    for chamada in resposta.tool_calls:
        registrar_span(chamada)  # ADR-006
        historico.append(executar(chamada))  # a tool pode mudar a etapa
```

Duas propriedades vêm de graça desse desenho:

**A pausa não é um mecanismo — é a ausência de um.** Quando a Aurora chega ao irreversível, a
tool `solicitar_aprovacao` grava uma linha em `pedidos_de_aprovacao` e devolve "aguardando". O
turno termina normalmente. Não há processo suspenso, não há continuação serializada, não há nada
para restaurar: o estado já **é** o banco. A Neuza aprovar é um `UPDATE`; o turno seguinte lê a
linha nova e segue. Reiniciar o container no meio não tem efeito, porque não havia nada na
memória.

**A continuidade entre canais é o mesmo mecanismo.** O chat web e o webhook do WhatsApp chamam a
mesma função com o mesmo `conversa_id`. O canal é uma coluna na mensagem, não um fluxo separado —
que é o que faz o handoff do [ADR-005](ADR-005-handoff-whatsapp-por-wa-me.md) funcionar sem
código de migração de sessão.

As tools disponíveis são **filtradas pela etapa** (`tools_da_etapa`). Na qualificação, a Aurora
não tem `solicitar_aprovacao` registrada — não porque o prompt proíba, mas porque a função não
está na lista daquele turno. É o mesmo princípio do [ADR-004](ADR-004-aprovacao-humana-no-irreversivel.md),
aplicado por etapa.

## Alternativas consideradas

### LangGraph com checkpointer em Postgres — **descartada, e não sem hesitação**

É a escolha certa para o problema geral, e o `interrupt` é uma primitiva melhor do que qualquer
coisa que eu escreva à mão. Se o EV-Sales tivesse três subagentes, ramificação condicional e
quatro pontos de aprovação, esta decisão seria a oposta.

Descartei por três motivos concretos, todos ancorados neste projeto:

1. **O checkpointer resolveria a durabilidade que o meu desenho já tem por outro caminho.** Meu
   estado precisa estar em tabelas legíveis de qualquer forma — a tela "Ler atendimento" do Raí
   ([ADR-006](ADR-006-observabilidade-e-teto-de-custo.md)) e a fila da Neuza leem essas tabelas
   direto. Com LangGraph, eu teria o estado **duas vezes**: nas minhas tabelas de domínio e no
   checkpoint serializado do grafo. Dois estados que podem divergir, e o Raí lendo o errado.
2. **Um ponto de interrupção não paga uma abstração de grafo.** A complexidade que o LangGraph
   administra é a de muitos nós; aqui há um `while` e um `switch` por etapa.
3. **Depurável pela equipe da Sol & Volt.** "Minha equipe consegue colocar pra rodar" é desejo
   explícito do enunciado. Um `SELECT * FROM conversas WHERE id = …` responde "onde essa conversa
   parou". Um checkpoint serializado exige a biblioteca para ser lido.

**O gatilho de revisão está escrito:** se surgir um segundo ponto de aprovação com ramificação, ou
um subagente com conjunto de permissões próprio, este ADR é reaberto. Migrar depois é caro, e eu
prefiro pagar caro depois com um motivo do que barato agora sem um.

### Framework de agente genérico (CrewAI, AutoGen e afins) — **descartada**

Resolvem colaboração entre múltiplos agentes, que é um problema que o EV-Sales não tem. Um agente
com sete tools não precisa de orquestração multiagente, e a camada extra atrapalha justo onde eu
mais preciso de controle: quais tools existem em cada etapa, e o que entra no prompt.

### Dois agentes — um de recomendação e um de fechamento — **descartada**

Separar por permissão (um só lê, o outro escreve) é um desenho legítimo e elegante. Descartado
porque a filtragem de tools por etapa entrega a mesma garantia — na etapa de qualificação, não há
tool de escrita registrada — sem o custo de transferir contexto entre dois agentes e sem o risco
de perder a nuance da conversa na passagem.

A garantia aqui não vem de quantos agentes existem; vem de qual função está na lista.

## Consequências

**Aceitas:**

- Escrevo e mantenho o loop, o retry e o limite de iterações. É código de infraestrutura que um
  framework daria pronto — algo como 150 linhas, e são 150 linhas que eu consigo explicar.
- Sem visualização de grafo pronta. A tela "Ler atendimento" cobre a necessidade real, e é feita
  para o Raí, não para mim.
- Se o produto crescer para múltiplos agentes, haverá uma migração. Gatilho declarado acima.
- Cada turno relê o histórico do Postgres. Custo de I/O real, irrelevante nesta escala, e é o que
  garante que o estado nunca diverge.

**Ganhas:**

- Uma dependência a menos numa parte do sistema que muda rápido.
- O estado da conversa é legível com SQL, por qualquer pessoa, sem biblioteca.
- Tools por etapa: o que a Aurora pode fazer é uma lista explícita por estado, revisável em diff.
- Durabilidade sem mecanismo: não há processo esperando, então não há processo para perder.
