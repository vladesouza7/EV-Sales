# ADR-011 — A jornada digital termina no test drive; a venda é presencial

**Status:** aceito
**Data:** 2026-09-03
**Substitui parcialmente:** o escopo assumido em [ADR-004](ADR-004-aprovacao-humana-no-irreversivel.md)

---

## Contexto

O recorte inicial do v1 dizia "proposta + reserva", com a palavra *proposta* carregando uma
ambiguidade que só apareceu quando o Raí descreveu o processo real dele.

**Vender um carro na Sol & Volt envolve, nesta ordem:**

```
condição acertada  →  financiamento aprovado pelo banco  →  faturamento na montadora
                                                                     │
                                                                     ▼
                             NF-e emitida  →  documentação (DUT/CRV)  →  entrega
```

Nada disso acontece na Sol & Volt sozinha. O faturamento é feito **contra a montadora**, com o
sistema dela; a NF-e sai do DMS que a concessionária já opera por obrigação fiscal; o financiamento
depende de aprovação de crédito de um banco parceiro; e a documentação segue o rito do Detran.

São quatro sistemas de terceiros, três deles obrigatórios por lei, todos já em funcionamento antes
de o EV-Sales existir.

Nas palavras do Raí:

> "Isso aí a gente já faz. Nota fiscal, faturamento, banco — é o sistema da representação, ou é
> direto com a fábrica. Não quero mexer nisso."

E há a restrição explícita do próprio desafio: **nada de documento com validade real**. Um sistema
que simulasse emissão de nota fiscal produziria um artefato que não é nem real nem útil — teatro
caro, com risco de alguém confundir com documento de verdade.

## Decisão

**A jornada do EV-Sales vai da chegada do cliente até o test drive agendado e realizado. A venda é
fechada presencialmente, no sistema que a Sol & Volt já tem.**

### O que a proposta passa a ser

Não é contrato, não é pedido, não é documento fiscal. É o **Espelho de Condição e Reserva** — o
equivalente digital do que a Neuza faz hoje no balcão quando diz *"segurei esse carro pra você até
sexta"*:

| Contém | Não contém |
|---|---|
| Modelo, versão, cor, ano, **chassi** | Condição de financiamento |
| Preço de tabela, lido do Postgres | Valor de entrada, parcela, taxa |
| Validade da condição: 7 dias | Dados de pagamento |
| Reserva do chassi: 72h | Qualquer campo fiscal |
| Data e hora do test drive, com o vendedor | Assinatura ou aceite formal |
| Endereço da loja e o que levar (CNH) | Promessa de entrega |

E a linha, sem eufemismo:

> *A negociação final, o financiamento e a documentação são tratados presencialmente na loja.*

### As duas aprovações, e por que só uma é nossa

| | Quem | Onde | Aprova o quê | No escopo? |
|---|---|---|---|---|
| **1ª** | Neuza | EV-Sales | Tirar o chassi do estoque e travar a condição escrita | **sim** |
| **2ª** | Raí ou Neuza | presencial, no DMS | A venda: preço final, financiamento, NF, documentação | não |

A primeira **continua existindo, e é o coração do [ADR-004](ADR-004-aprovacao-humana-no-irreversivel.md)**.
Reduzir o escopo da venda não reduz o irreversível: o chassi que sai do estoque continua saindo
para todos os outros clientes, e continua sendo o telefonema que o Raí não quer dar
([CASE — fala 2](../CASE.md#o-que-ele-disse-com-as-palavras-dele)).

O que mudou foi o **objeto** da aprovação, não o mecanismo. A Neuza deixa de aprovar "emissão de
documento de venda" e passa a aprovar "esse carro sai do estoque para esse cliente, por 72h, com
esse preço escrito". O `approval_id NOT NULL`, a expiração de 20 minutos e a tela de 30 segundos
continuam idênticos.

### O que volta para dentro do sistema: o desfecho

Este é o ponto que não é opcional. Se a venda acontece no DMS e ninguém informa o EV-Sales, o
chassi fica `reservado` para sempre e **o catálogo passa a mentir** — o que destrói a premissa do
[ADR-001](ADR-001-postgres-fonte-da-verdade.md).

Depois do test drive, o vendedor marca o desfecho numa tela de um toque
([S-07](../spec/S-07-test-drive.md)):

```
  compareceu?   [ sim ]  [ não ]
       │
       ▼
  desfecho:  [ vendeu ]  [ vai pensar ]  [ desistiu ]
                  │            │              │
                  ▼            ▼              ▼
              vendido     reserva mantida  liberado
                          até o prazo
```

`vendeu` marca a unidade como `vendido` e encerra o lead como ganho. É a única informação que
atravessa a fronteira entre o EV-Sales e o processo presencial, e ela vai **na mão**, não por
integração.

### O artefato que ganha valor: o dossiê do vendedor

Com a venda fora do escopo, o documento mais útil que o sistema produz deixa de ser um PDF para o
cliente e passa a ser um **resumo para o Tarcísio**, aberto no celular antes do test drive:

```
Tarcísio Nóbrega · quinta, 14h · BYD Seal branco …4471 · R$ 249.990

Rotina    40 km/dia · Manaíra→Centro · carregador em casa (tomada comum)
Orçamento até R$ 260 mil · não falou em financiamento
Perfil    primeiro elétrico · nunca dirigiu um

⚠ Objeções levantadas
  · medo de ficar sem bateria na estrada — a Aurora explicou com a rotina dele
  · perguntou duas vezes sobre vida útil da bateria

💡 Não perguntou sobre financiamento. Vale abrir o assunto.
```

Hoje o Tarcísio recebe "lead quente" e começa a conversa do zero, repetindo perguntas que o cliente
já respondeu. Isso é documento emitido com valor comercial real, e sem nenhuma implicação fiscal.

## Alternativas consideradas

### Simular a venda completa, com NF-e simulada — **descartada**

Era o caminho para "cobrir tudo o que o enunciado cita". Descartada por três motivos.

O primeiro é que o enunciado **proíbe documento com validade real** e pede simulação fiel — e uma
simulação fiel de NF-e é um arquivo que se parece com uma nota fiscal sem ser uma, que é
exatamente o tipo de artefato que alguém acaba imprimindo e mostrando para um cliente.

O segundo é que seria trabalho de fachada: emitir uma NF simulada não exercita nenhum risco real do
domínio. Todo o valor de engenharia deste projeto — preço que não sai do modelo, corrida por
chassi único, aprovação humana, PII — já está antes desse ponto.

O terceiro é honestidade de portfólio: um sistema que "vende carro pelo chat" descreve algo que não
existe. Ninguém compra um carro de R$ 250 mil sem sentar dentro dele.

### Pagamento de sinal em sandbox — **descartada**

Era a opção que eu mesmo tinha recomendado na definição de escopo, e o desafio libera ambiente de
teste de meio de pagamento.

Perdeu quando ficou claro que **na Sol & Volt não existe sinal antes do test drive**. O cliente
dirige, gosta, aí negocia. Cobrar sinal para reservar um carro que a pessoa nunca viu não é o
processo do Raí — seria um requisito inventado para o sistema ficar mais completo, e requisito
inventado é o oposto do que este desafio pede.

Fica registrado como candidato de v2, num ponto diferente do funil: sinal **depois** do test drive,
para segurar a unidade durante a análise de crédito.

### Integrar com o DMS da concessionária — **descartada para o v1**

O caminho "certo" a longo prazo: o EV-Sales empurra o lead qualificado e recebe o desfecho
automaticamente. Descartado porque depende de um sistema de terceiro cuja API eu não conheço, e
porque a Sol & Volt vende de 7 a 11 carros por mês — o desfecho manual custa **um toque, onze vezes
por mês**.

Automatizar onze cliques mensais ao custo de uma integração é a definição de complexidade sem
retorno. O gatilho de revisão: quando passar de ~40 vendas/mês, ou quando a marcação manual for
esquecida mais de duas vezes num mês (medido, porque catálogo mentindo é falha grave).

### Parar antes, no lead qualificado, sem reserva — **descartada**

Entregar o lead para o vendedor e encerrar. Seria mais simples, e destruiria o projeto: sem reserva
não há irreversível, sem irreversível não há aprovação humana, e o desafio pede explicitamente
*"quero aprovar aquilo que não tem volta"*. A reserva é o que torna este um agente de vendas e não
um formulário conversacional.

## Consequências

**Aceitas:**

- **O sistema não processa pagamento nem emite documento fiscal.** Um leitor apressado do enunciado
  pode ler isso como escopo incompleto. A defesa está no PRD e no README, e ela é a mesma que eu
  daria numa entrevista: no domínio de veículos, o fim da jornada digital **é** o test drive, e
  fingir o contrário produziria demonstração, não produto.
- **A marcação de desfecho é manual e pode ser esquecida.** Mitigado: lembrete automático ao
  vendedor 2h após o horário do test drive, e alerta se uma reserva vence sem desfecho registrado.
- **O EV-Sales não sabe a receita gerada**, só o test drive realizado e o marcador de venda. A
  métrica financeira fica no DMS.
- Fica uma fronteira manual entre dois sistemas. É uma fronteira honesta: ela existe no negócio real.

**Ganhas:**

- Some a necessidade de simular documento fiscal, e com ela some a categoria inteira de risco de
  alguém confundir simulação com documento válido.
- O escopo fica alinhado ao processo real da Sol & Volt, o que é a diferença entre um sistema que
  a equipe do Raí usa e um que ela contorna.
- O esforço de engenharia se concentra onde estão os riscos de verdade: número que não sai do
  modelo, corrida por chassi, aprovação humana e PII.
- O dossiê do vendedor entrega valor comercial imediato, e não tem nenhuma implicação fiscal.
