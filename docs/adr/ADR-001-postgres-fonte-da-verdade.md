# ADR-001 — Postgres como fonte da verdade de estoque e preço

**Status:** aceito
**Data:** 2026-09-03
**Decide sobre:** [CASE.md — fala 2 do Raí](../CASE.md#o-que-ele-disse-com-as-palavras-dele)

---

## Contexto

O Raí disse, mais de uma vez:

> "Carro eu tenho um de cada. Se esse negócio prometer o mesmo Seal branco pra duas pessoas,
> alguém vai ter que ligar pra uma delas e desmarcar. E esse alguém sou eu."

Isso não é uma preferência de modelagem, é a física do negócio. A Sol & Volt não vende SKU com
quantidade, vende **unidade identificada por chassi**. O Seal branco `…4471` e o Seal cinza
`…9902` são o mesmo modelo, o mesmo preço de tabela e produtos completamente diferentes no
momento da reserva.

Consequência direta: a operação crítica do sistema é

> reservar o chassi X **se e somente se** ele ainda estiver disponível,

com dois clientes possivelmente clicando no mesmo segundo — um pelo chat web, outro pelo
WhatsApp. É uma corrida clássica, e o v1 precisa vencê-la por construção, não por sorte de
timing.

Eu tinha proposto MongoDB como alternativa na fase de arquitetura, e é essa a decisão sendo
registrada aqui.

## Decisão

**PostgreSQL 17 é a única fonte da verdade sobre estoque, chassi, preço e estado de reserva.**

A reserva é uma única instrução condicional dentro de uma transação:

```sql
UPDATE unidades
   SET status = 'reservado',
       reservado_para = :lead_id,
       reservado_em   = now()
 WHERE chassi = :chassi
   AND status = 'disponivel'   -- ← a corrida é decidida aqui
RETURNING chassi;
```

Zero linhas retornadas significa que outra pessoa chegou primeiro, e o sistema responde isso ao
cliente. Não há leitura-depois-escrita, não há checagem na aplicação, não há janela entre
verificar e reservar.

Além disso: `status` é enum no banco, `preco_centavos` é `bigint` (nunca float), e existe
`EXCLUDE`/índice parcial garantindo no máximo uma reserva ativa por chassi. As regras que
protegem o Raí moram no schema, não no código de aplicação — porque código de aplicação eu
reescrevo com um agente numa tarde, e schema exige migration revisada.

## Alternativas consideradas

### MongoDB — **descartada**

Foi a alternativa que eu mesmo levantei, e ela tinha argumentos reais: o catálogo de veículos é
heterogêneo entre marcas (o Volvo tem campos que o JAC não tem), documento aninhado modelaria
isso sem tabela de atributos, e a conversa é naturalmente um documento.

Descartei por uma razão só, e ela é suficiente: **o erro que mais aterroriza o Raí é uma
condição de corrida**, e no Mongo a proteção contra ela seria uma decisão minha, repetida
corretamente em cada caminho de código que toca estoque. No Postgres é uma constraint que
recusa a segunda gravação mesmo se o meu código estiver errado, mesmo se um agente de código
introduzir um caminho novo que eu não revisei direito.

O ganho da modelagem heterogênea é real — e com o catálogo de 18 marcas e ~58 modelos
([lista](../pesquisa/MARCAS-E-MODELOS-BR.md)) ele ficou **maior** do que quando escrevi este ADR:
um Porsche tem campos que um JAC não tem. Ainda assim é pequeno perto do que se perde: um
`JSONB` em `unidades.atributos` resolve o que varia entre marcas — dentro do mesmo banco que dá
a transação. Eu ficaria com a flexibilidade do documento e com a garantia relacional.

Descartar Mongo também elimina uma pergunta futura desconfortável: com Mongo, onde ficaria a
proposta comercial, que precisa ser consistente com o chassi no mesmo instante?

### Postgres para transacional + Mongo para conversas — **descartada**

Dois bancos, dois backups, dois pontos de falha e a chance de a conversa e o estado do lead
divergirem. As mensagens cabem numa tabela com `JSONB` para o payload cru do WhatsApp. Não paguei
o custo operacional de um segundo banco para ganhar conforto de esquema.

### Redis como estado de conversa autoritativo — **descartada como fonte da verdade**

Redis fica no projeto, mas exclusivamente como cache e fila ([ARQUITETURA](../ARQUITETURA.md)).
O Raí precisa reabrir uma conversa de três semanas atrás e ler. Estado que precisa sobreviver é
estado que mora em disco relacional; Redis expira, e a fala 4 do Raí não tolera expiração.

## Consequências

**Aceitas:**

- O catálogo multimarca precisa de `JSONB` para os atributos que variam. Menos elegante que
  documento nativo, e é o preço da transação.
- Toda mudança de estrutura passa por migration Alembic revisada. Mais lento de propósito: é
  exatamente aqui que eu não quero um agente de código improvisando.
- Postgres precisa estar de pé para o sistema atender. Aceito: sem estoque confiável, atender é
  pior do que não atender.

**Ganhas:**

- Reserva dupla vira **impossível**, não improvável. Testável com um teste de concorrência que
  dispara N reservas simultâneas do mesmo chassi e exige exatamente 1 sucesso ([S-05](../spec/S-05-reserva-de-chassi.md)).
- pgvector mora no mesmo banco, o que dispensa o Qdrant no v1 ([ADR-002](ADR-002-pgvector-em-vez-de-qdrant.md)).
- Um serviço de dado a menos para a equipe da Sol & Volt operar — e "minha equipe consegue
  colocar pra rodar" é desejo explícito do cliente.
