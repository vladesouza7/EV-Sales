# O Case — Sol & Volt Veículos Elétricos

> Este documento é a fonte da verdade sobre **quem** é o negócio, **quem** contratou, **quem**
> é atendido e **como o produto se chama**. Toda spec, ADR, prompt e mensagem de commit deste
> repositório usa estes nomes. Se um nome aparece aqui, ele não é ilustrativo — ele é o sistema.

---

## O negócio

**Sol & Volt Veículos Elétricos**
Concessionária multimarca de veículos elétricos e híbridos plug-in
Av. Epitácio Pessoa, 2.140 — Tambaú, João Pessoa/PB
Fundada em março de 2023

O nome veio da piada que virou slogan: João Pessoa é a cidade onde o sol nasce primeiro nas
Américas, e a Sol & Volt vende o carro que se abastece com esse sol. Metade da carteira de
clientes tem placa solar em casa — e o argumento de venda mais forte da loja não é autonomia,
é o custo por quilômetro de quem gera a própria energia e niguem vai aduterar.

**O que a Sol & Volt tem hoje:**

| | |
|---|---|
| Showroom | 11 vagas, Tambaú |
| Estoque médio | 14 a 19 unidades, **cada uma com chassi próprio** |
| Catálogo | **18 marcas · ~58 modelos** ([lista](pesquisa/MARCAS-E-MODELOS-BR.md)) |
| Novos | BYD, GWM, JAC, GAC (Aion), Leapmotor, Geely/Zeekr — as chinesas |
| Seminovos | Volvo, BMW, Audi, Porsche, Mini, Chevrolet, Renault, Nissan, Peugeot, Hyundai, Kia, **Tesla** |
| Faixa de preço | R$ 118.900 (Dolphin Mini, novo) a ~R$ 890.000 (Taycan, seminovo) |
| Equipe comercial | 2 vendedores + 1 gerente |
| Vendas | 7 a 11 unidades/mês |
| Conversas recebidas | ~280/mês (Instagram DM, WhatsApp, formulário do site) |

A regra é simples e é comercial: **novo só das chinesas.** BYD, GWM, JAC, GAC, Leapmotor e Zeekr
têm rede fina no Nordeste e operam por revenda multimarca — foi essa brecha que o Raí enxergou em
2023. Todo o resto tem concessão de fábrica exclusiva, que a Sol & Volt não tem, e chega a ela como
**seminovo**, por troca e repasse.

A Tesla é o caso extremo: ela vende direto ao cliente, sem concessionária nenhuma. **Não existe
caminho para um Tesla novo na Sol & Volt** — e é justamente a marca que mais gente pede pelo nome.

**O detalhe que define a arquitetura deste projeto:** a Sol & Volt não vende SKU, vende
**unidade**. Não existe "3 Seal em estoque" — existe o Seal branco chassi `…4471` e o Seal
cinza chassi `…9902`. Quando um cliente reserva, aquele chassi sai do estoque para todos os
outros, e não há segunda unidade idêntica para desempatar. É por isso que reserva, neste
domínio, é irreversível de verdade — e não uma irreversibilidade inventada para o exercício.

O seminovo leva isso ao limite: aquele Taycan tem aquele chassi, aquela quilometragem, aquele
histórico e aquele preço. **Não existe "outro igual" nem na teoria** — e o preço passa a ser por
unidade, não por modelo, o que é mais um motivo para número nenhum sair do modelo de linguagem
([ADR-003](adr/ADR-003-numeros-nunca-saem-do-modelo.md)).

---

## O cliente que me contratou

**Raimundo Falcão, 58 anos** — "Seu Raí" para a equipe, "Raimundo" só no cartório.

A família dele vendeu carro a combustão em João Pessoa por 35 anos: a Falcão Automóveis
funcionou de 1987 a 2022, na mesma quadra da Epitácio. Em 2022 ele fechou a loja do pai,
vendeu o ponto e abriu a Sol & Volt com o dinheiro da venda. Foi a aposta da vida dele, e ele
fala disso com todas as letras: *"eu não tenho um segundo negócio pra amortecer se esse aqui
der errado."*

Raí não é avesso a tecnologia — ele é avesso a **tecnologia que ele não entende**. Aprovou o
projeto na quarta reunião, quando perguntei se ele queria poder abrir um atendimento antigo e
ler o que aconteceu ali dentro, e ele respondeu que era exatamente isso que faltava nos dois
orçamentos anteriores que tinha recebido.

### O que ele disse, com as palavras dele

Estas seis falas são o requisito bruto. Cada uma vira uma decisão rastreável neste repositório:

1. **Sobre alucinação** → [ADR-003](adr/ADR-003-numeros-nunca-saem-do-modelo.md)
   > "Se esse robô disser pro cliente que o Dolphin faz 400 quilômetro e ele faz 291, o cara
   > descobre no test drive. Eu não perco só a venda, eu perco a cara. Em João Pessoa todo
   > mundo se conhece."

2. **Sobre estoque** — *o medo que ele repetiu mais vezes* → [ADR-001](adr/ADR-001-postgres-fonte-da-verdade.md)
   > "Carro eu tenho um de cada. Se esse negócio prometer o mesmo Seal branco pra duas
   > pessoas, alguém vai ter que ligar pra uma delas e desmarcar. E esse alguém sou eu."

3. **Sobre documento e desconto** → [ADR-004](adr/ADR-004-aprovacao-humana-no-irreversivel.md)
   > "Proposta com preço escrito não sai sem a Neuza ver. E desconto quem dá sou eu — não é o
   > robô, não é o vendedor, não é ninguém."

4. **Sobre auditoria** → [ADR-006](adr/ADR-006-observabilidade-e-teto-de-custo.md)
   > "Se der ruim eu quero abrir a conversa e ler. Do começo ao fim. Não quero explicação de
   > engenheiro, quero ler o que foi dito."

5. **Sobre dados do cliente** → [ADR-007](adr/ADR-007-pii-cifrada-e-mascarada.md)
   > "Telefone de cliente é o meu ativo. Foi o que sobrou da loja do meu pai. Se vazar, meu
   > concorrente liga pra todo mundo na segunda-feira."

6. **Sobre custo** → [ADR-006](adr/ADR-006-observabilidade-e-teto-de-custo.md)
   > "Não quero descobrir dia 30 que gastei quatro mil real de inteligência artificial. Quero
   > saber quanto custa, e quero um teto."

---

## Quem coloca a mão no sistema

**Neuza Andrade, 44 anos — gerente de vendas.**
19 anos de showroom, 7 deles com o Raí ainda na Falcão Automóveis. É ela quem bate o martelo
em preço, condição e entrega. **A Neuza é o humano no loop deste projeto:** nenhuma proposta
comercial e nenhuma reserva de chassi existe sem o "aprovado" dela.

Neuza é o motivo pelo qual várias decisões deste repositório foram tomadas como foram. Ela
trabalha com o celular na mão entre um cliente e outro, não com um dashboard aberto — então a
fila de aprovação precisa ser lida e resolvida em menos de 30 segundos, no telefone, entre
dois atendimentos presenciais. Uma tela de aprovação que exige computador é uma tela que não
vai ser usada; e proposta que trava esperando aprovação é venda perdida. Esse par de
restrições opostas é o problema de design mais difícil do projeto, e está em
[S-04](spec/S-04-fila-de-aprovacao.md).

**Tarcísio Lima (29) e Jaqueline Souto (35) — vendedores.**
Assumem o atendimento quando o lead esquenta ou quando o cliente pede gente. Não aprovam
proposta, não alteram preço, não liberam desconto. Fazem o test drive.

---

## O produto que estou construindo

# EV-Sales
**A consultora se chama Aurora.**

EV-Sales é o sistema. Aurora é com quem o cliente conversa — e ela se apresenta pelo nome, diz
que é assistente da Sol & Volt e nunca finge ser humana. Quando perguntam se ela é uma pessoa,
ela responde que não e oferece chamar o Tarcísio ou a Jaqueline.

### O jeito da Aurora

Aurora não é uma FAQ com personalidade. Ela é uma **consultora que qualifica antes de
recomendar**, porque no domínio dela recomendar cedo é recomendar errado: o cliente que chega
dizendo "quero um Tesla" quase sempre quer, na verdade, um carro que faça João Pessoa–Recife
sem parar — e isso muda a recomendação e a faixa de preço.

| Aurora faz | Aurora não faz |
|---|---|
| Pergunta o uso antes de sugerir modelo | Despejar o catálogo na primeira mensagem |
| Diz "não temos esse em estoque hoje" | Empurrar o que tem fingindo que é o ideal |
| Consulta o preço no banco antes de falar | Estimar, arredondar ou lembrar preço |
| Reconhece que é uma IA quando perguntam | Se passar por Tarcísio ou Jaqueline |
| Explica autonomia com o número do Inmetro | Repetir número WLTP como se fosse real |
| Passa para humano quando pedem | Insistir depois de um "não" |

Uma regra de estilo que virou requisito: **Aurora fala em quilômetro por dia, não em
quilowatt-hora.** O cliente da Sol & Volt não sabe o que são 60 kWh; ele sabe que vai de
Manaíra ao Centro todo dia. Toda especificação técnica é traduzida para a rotina que o cliente
acabou de descrever.

---

## Quem é atendido

### Persona 1 — "O primeiro elétrico" · ~55% das conversas
**Tarcísio Nóbrega, 31, desenvolvedor remoto, mora em Manaíra.**
Roda 40 km/dia, garagem com tomada, orçamento até R$ 150 mil. Nunca dirigiu um elétrico e tem
medo de ficar sem bateria — mesmo com uma rotina que cabe folgada em qualquer modelo da loja.
Não sabe a diferença entre autonomia Inmetro e WLTP, e vai comparar o número que a Aurora
falar com o número que ele viu num vídeo do YouTube.
**O que ele precisa:** ser convencido de que 280 km é muito para quem roda 40.
**Onde ele desiste hoje:** manda mensagem às 21h, recebe resposta às 10h do dia seguinte.

### Persona 2 — "A família que viaja" · ~30% das conversas
**Fernanda e Diego Barreto, 38 e 41, dois filhos, moram no Bessa.**
Uso misto: cidade na semana, Recife uma vez por mês, Pipa no feriado. Orçamento R$ 200–280
mil. A pergunta real deles não é autonomia, é **onde carregar na BR-101**. Decidem juntos, e a
Fernanda é quem pesquisa.
**O que eles precisam:** rota, não ficha técnica.
**Onde eles desistem hoje:** ninguém responde a pergunta da BR-101, respondem a autonomia.

### Persona 3 — "O que já decidiu" · ~15% das conversas
**Dr. Almir Cavalcanti, 52, cirurgião, Cabo Branco.**
Chega dizendo o modelo e a cor. Quer saber preço, prazo de entrega e se tem em estoque — nessa
ordem, em três mensagens. Qualificação consultiva com ele é atrito.
**O que ele precisa:** número certo, rápido, e um humano em seguida.
**Onde ele desiste hoje:** recebe "vou verificar com o setor" e compra em Recife.

> As três personas exigem coisas opostas da mesma conversa. É por isso que o EV-Sales qualifica
> de forma adaptativa e não por formulário fixo: perguntar rotina e uso para o Dr. Almir custa
> a venda tanto quanto pular a qualificação com o Tarcísio.

---

## A jornada completa, e onde ela pode dar errado

```
   chegada        qualificação     recomendação      espelho de       reserva      test drive
      │                │                │             condição           │             │
   landing         Aurora           Aurora          Aurora monta    chassi sai    Tarcísio ou
   do site      pergunta uso,      consulta        → NEUZA APROVA → do estoque →  Jaqueline
      │         rotina, orçam.    o Postgres            │               │          atendem
      │                │                │               │               │             │
  ┌───┴───┐       ┌────┴────┐      ┌────┴────┐     ┌────┴────┐    ┌─────┴────┐  ┌─────┴────┐
  │ risco │       │  risco  │      │  risco  │     │  risco  │    │  risco   │  │  risco   │
  │ baixo │       │  baixo  │      │  ALTO   │     │  ALTO   │    │  MÁXIMO  │  │  baixo   │
  └───────┘       └─────────┘      └─────────┘     └─────────┘    └──────────┘  └──────────┘
                                    inventar        desconto       prometer o
                                 preço/autonomia  não autorizado   mesmo chassi
                                                                    duas vezes

 ══════════════════ até aqui é o EV-Sales ══════════════════════════════════════╪═══════
                                                                                │
                                              desfecho ◀── 1 toque do vendedor ──┘
                                              (vendeu / vai pensar / desistiu)
                                                     │
                                                     ▼
                                    A VENDA acontece presencialmente, no
                                    sistema que a Sol & Volt já tem:
                                    negociação final, financiamento,
                                    faturamento na montadora, NF-e,
                                    documentação
```

**Onde a jornada digital termina, e por quê.** Ninguém compra um carro de R$ 250 mil sem sentar
dentro dele. O fim do que o EV-Sales faz é colocar um cliente qualificado dentro do carro certo,
com um vendedor que já sabe o que ele precisa. O resto — nota fiscal, faturamento contra a
montadora, crédito aprovado por banco, documentação do Detran — já acontece na Sol & Volt, por
obrigação legal, em sistemas que existem antes deste projeto
([ADR-011](adr/ADR-011-jornada-digital-termina-no-test-drive.md)).

A única coisa que atravessa a fronteira de volta é o **desfecho**: se o chassi foi vendido. Sem
isso o catálogo passa a mentir, e catálogo mentindo é o medo nº 2 do Raí voltando pela porta dos
fundos.

**Onde linguagem natural gera valor:** chegada, qualificação, quebra de objeção, follow-up. São
etapas em que errar custa uma frase e se corrige na mensagem seguinte.

**Onde linguagem natural gera risco:** todo ponto em que sai um **número** (preço, autonomia,
prazo, parcela) ou em que o mundo **muda de estado** (proposta emitida, chassi reservado).
Nesses pontos o modelo escolhe as palavras; quem escolhe o valor é o banco, e quem autoriza a
mudança de estado é a Neuza.

> **A regra que costura o projeto inteiro:**
> o modelo de linguagem decide o que dizer; o código decide o que pode ser feito.
