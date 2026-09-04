# Marcas e modelos elétricos no Brasil — referência de mercado

> **O que este documento é:** o universo de marcas de veículos elétricos com operação no Brasil, e
> os modelos de cada uma. É pesquisa de mercado, insumo do seed do catálogo
> ([S-10](../spec/S-10-operacao.md)).
>
> **O que ele não é:** a lista do que a Sol & Volt tem em estoque. O estoque real está em
> `unidades`, é de 14 a 19 carros, e cada um tem chassi. A distinção entre `modelos` (o que existe
> no mercado) e `unidades` (o que está no pátio) é a mesma do
> [ADR-001](../adr/ADR-001-postgres-fonte-da-verdade.md), e é o que impede a Aurora de oferecer um
> carro que a loja não tem.

**18 marcas · ~58 modelos**

---

## 🇨🇳 Marcas chinesas

| Marca | Modelo | Categoria |
|---|---|---|
| **BYD** | Dolphin Mini | Hatchback subcompacto |
| | Dolphin | Hatchback compacto |
| | Yuan Pro | SUV compacto |
| | Yuan Plus | SUV compacto |
| | Song Plus | SUV híbrido plug-in / elétrico |
| | Seal | Sedã esportivo |
| | Han | Sedã de luxo |
| | Tan | SUV 7 lugares |
| **GWM** (Great Wall Motors) | Ora 03 | Hatchback compacto premium |
| **JAC Motors** | E-JS1 | Subcompacto urbano |
| | E-JS4 | SUV compacto |
| | E-J7 | Sedã médio |
| **Geely** *(reestreia e expansão no mercado nacional)* | Zeekr 001 | Shooting brake elétrico |
| | Zeekr X | SUV compacto |
| **GAC (Aion)** | Aion Y Plus | SUV elétrico |
| | Aion S | Sedã elétrico |
| | Aion RT | Sedã elétrico |
| **Leapmotor** *(no Brasil sob o grupo Stellantis)* | T03 | Subcompacto urbano |
| | C10 | SUV médio |

> **Nota sobre a Geely:** o grupo controla também a Volvo e a Zeekr, o que significa que plataformas
> e componentes se repetem entre marcas que a Aurora apresenta como concorrentes. É informação de
> conhecimento (`buscar_conhecimento`), não de catálogo — útil quando o cliente pergunta *"o Zeekr é
> bom igual ao Volvo?"*.

---

## 🇪🇺 Marcas europeias premium

| Marca | Modelo | Categoria |
|---|---|---|
| **Volvo** | EX30 | SUV compacto de entrada |
| | XC40 Recharge / EX40 | SUV compacto |
| | C40 Recharge / EC40 | SUV cupê |
| | EX90 | SUV de luxo 7 lugares |
| **BMW** | iX1 | SUV compacto |
| | iX3 | SUV médio |
| | i4 | Gran Coupé |
| | i5 | Sedã de luxo |
| | i7 | Sedã de luxo |
| | iX | SUV topo de linha |
| **Audi** | Q4 e-tron | SUV médio |
| | Q8 e-tron | SUV grande |
| | Q8 Sportback e-tron | SUV cupê grande |
| | e-tron GT | Esportivo de alta performance |
| | RS e-tron GT | Esportivo de alta performance |
| **Porsche** | Taycan | Esportivo de luxo |
| | Taycan Cross Turismo | Perua esportiva de luxo |
| | Macan Elétrico | SUV esportivo |
| **Mini** | Cooper E | Hatchback compacto |
| | Cooper SE | Hatchback compacto |
| | Countryman SE ALL4 | SUV compacto |

---

## 🇺🇸 🇫🇷 🇯🇵 Marcas tradicionais de volume

| Marca | Modelo | Categoria |
|---|---|---|
| **Chevrolet** | Bolt EV | Hatchback — pioneiro da marca |
| | Bolt EUV | Crossover — pioneiro da marca |
| | Blazer EV | SUV médio-grande |
| | Equinox EV | SUV médio |
| **Renault** | Kwid E-Tech | Subcompacto de entrada |
| | Zoe | Hatchback pioneiro |
| | Megane E-Tech | Crossover compacto |
| **Nissan** | Leaf | Hatchback — pioneiro mundial |
| | Ariya | SUV médio |
| **Peugeot** | e-208 GT | Hatchback esportivo |
| | e-2008 | SUV compacto |

---

## 🇺🇸 🇰🇷 Venda direta e coreanas

| Marca | Modelo | Categoria |
|---|---|---|
| **Tesla** *(venda direta — sem concessionária)* | Model 3 | Sedã |
| | Model Y | SUV médio |
| **Hyundai** | Kona Elétrico | SUV compacto |
| | Ioniq 5 | Crossover — arquitetura 800V |
| | Ioniq 6 | Sedã aerodinâmico |
| **Kia** | Niro EV | Crossover compacto |
| | EV6 | Crossover — arquitetura 800V |

> **A Tesla é um caso à parte, e vira regra de código.** Ela não opera por concessionária no Brasil:
> vende direto. **A Sol & Volt nunca vai ter um Tesla novo** — só seminovo, por troca ou repasse.
>
> Isso importa porque a Tesla é a marca que o cliente pede **pelo nome**, sem ter pesquisado o resto
> do mercado. É a cena de qualificação que abre o [CASE](../CASE.md#o-jeito-da-aurora): quem diz
> *"quero um Tesla"* quase sempre quer, na verdade, um carro que faça João Pessoa–Recife sem parar.
> A Aurora precisa dizer a verdade sobre a disponibilidade e conduzir a conversa para o uso, sem
> empurrar substituto fingindo que é a mesma coisa ([S-03](../spec/S-03-agente-aurora.md)).

---

## O que a Sol & Volt vende deste universo

Uma loja não vende as 18 marcas do mesmo jeito, e a divisão é comercial, não técnica:

| Origem | Marcas | Por quê |
|---|---|---|
| **Novos** | BYD, GWM, JAC, GAC (Aion), Leapmotor, Geely/Zeekr | Marcas chinesas em expansão, com rede fina no Nordeste e operação por revenda multimarca. É o negócio que o Raí abriu em 2023 |
| **Seminovos** | Volvo, BMW, Audi, Porsche, Mini, Chevrolet, Renault, Nissan, Peugeot, Hyundai, Kia | Concessão de fábrica exclusiva — a Sol & Volt não tem, e chega a ela por troca e repasse |
| **Seminovos, sempre** | Tesla | Venda direta, sem concessionária. Não existe caminho para "novo" |

> **Correção de uma inconsistência anterior:** a primeira versão deste documento listava Chevrolet,
> Renault, Nissan e Peugeot como novos, o que estava errado — essas marcas têm concessão exclusiva
> tanto quanto BMW ou Audi. A regra correta é simples: **novo só das chinesas; todo o resto é
> seminovo.**

**Isso reforça a tese central do projeto em vez de complicá-la.** Um seminovo é *intrinsecamente*
uma unidade única: aquele Taycan tem aquele chassi, aquela quilometragem, aquele histórico e aquele
preço. Não existe "outro igual" nem na teoria. O
[ADR-001](../adr/ADR-001-postgres-fonte-da-verdade.md) fica mais verdadeiro, não menos — e como a
maior parte do catálogo é seminovo, ele vale para a maior parte do estoque.

Consequências no schema ([S-03](../spec/S-03-agente-aurora.md)):

- `unidades.condicao` — `'novo' | 'seminovo'`
- `unidades.km` — já existia; passa a importar de verdade
- `preco_centavos` mora em **`unidades`, não em `modelos`**: dois Taycan do mesmo ano têm preços
  diferentes conforme km e histórico. Preço é atributo da unidade, e é mais um motivo para ele nunca
  sair do modelo de linguagem ([ADR-003](../adr/ADR-003-numeros-nunca-saem-do-modelo.md))

---

## O que ainda falta para o seed

Este documento tem marca, modelo e categoria. O seed do [S-10](../spec/S-10-operacao.md) precisa,
por modelo:

```
autonomia_km  +  autonomia_fonte  (INMETRO_PBEV_2026 · WLTP · FABRICANTE)
bateria_kwh   ·  potencia_cv  ·  tempo_carga_dc_min  ·  tracao
faixa de preço de referência
```

### `autonomia_fonte` é o campo mais perigoso do catálogo

Não é burocracia ([ADR-003](../adr/ADR-003-numeros-nunca-saem-do-modelo.md)). Repare no que a
pesquisa anterior ([CATALOGO-E-OBJECOES.md](CATALOGO-E-OBJECOES.md)) traz para as marcas que
acabaram de entrar:

| Modelo | Número publicado | Fonte |
|---|---|---|
| Tesla Model Y | 533 km | **WLTP** |
| Tesla Model 3 | 580–602 km | **WLTP** |
| Hyundai Ioniq 6 | 614 km | **WLTP** |
| Hyundai Ioniq 5 | 507 km | **WLTP** |
| Kia EV6 | 528 km | **WLTP** |
| BYD Seal | 372 km | Inmetro/PBEV |
| BYD Dolphin | 291 km | Inmetro/PBEV |

**Todos os números importados são WLTP, e todos os chineses são Inmetro.** O WLTP é
sistematicamente mais otimista. Cadastrar os dois na mesma coluna sem marcar a origem faria a Aurora
comparar 614 km com 291 km como se fossem a mesma medida — e prometer autonomia que o carro não
entrega na BR-101.

Isso é o medo nº 1 do Raí entrando pela porta dos **dados** em vez da porta do modelo, e é o tipo de
erro que a verificação numérica na saída **não pegaria**, porque o número estaria "correto" segundo
o banco.

Modelo sem autonomia Inmetro confirmada entra no seed com `autonomia_km = NULL`, e a Aurora responde
*"não tenho esse número confirmado, vou verificar com o vendedor"* — nunca estima, nunca converte
WLTP em Inmetro por regra de três.
