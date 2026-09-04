# ADR-003 — Nenhum número sai do modelo

**Status:** aceito
**Data:** 2026-09-03
**Decide sobre:** [CASE.md — fala 1 do Raí](../CASE.md#o-que-ele-disse-com-as-palavras-dele)

---

## Contexto

> "Se esse robô disser pro cliente que o Dolphin faz 400 quilômetro e ele faz 291, o cara
> descobre no test drive. Eu não perco só a venda, eu perco a cara."

O domínio de veículo elétrico é especialmente hostil a alucinação numérica, por três motivos que
não existem em outros ramos:

1. **Autonomia tem três números diferentes para o mesmo carro.** Inmetro/PBEV, WLTP e o número
   real na BR-101 são valores distintos, e a internet mistura os três. Um modelo de linguagem
   treinado nessa internet vai devolver o mais otimista, porque é o mais citado.
2. **O cliente confere.** A Persona 1 assiste review no YouTube antes de entrar no showroom.
3. **O erro aparece depois da venda**, no test drive ou na primeira viagem — quando já custou a
   reputação, não só a conversa.

E o mesmo vale para preço: a tabela muda com câmbio e campanha de fábrica, mais de uma vez por
trimestre.

## Decisão

**Nenhum valor numérico do domínio entra no contexto do modelo como texto livre, e nenhum sai
dele por geração.** Preço, autonomia, potência, prazo de entrega, quantidade e disponibilidade
vêm exclusivamente do retorno de uma tool que leu o Postgres.

Três mecanismos, em camadas:

**1. O catálogo não está no prompt.** O prompt do sistema descreve como a Aurora conversa, não o
que a Sol & Volt vende. Não há lista de modelos com preços no system prompt — porque o que está
no prompt o modelo parafraseia, e parafrasear número é arredondar número.

**2. Os números chegam por tool, com marcação de origem.** A tool devolve estruturado:

```json
{
  "chassi": "…4471",
  "modelo": "BYD Seal Premium",
  "preco_centavos": 24999000,
  "autonomia_km": 372,
  "autonomia_fonte": "INMETRO_PBEV_2026",
  "status": "disponivel",
  "consultado_em": "2026-09-03T14:22:07-03:00"
}
```

O campo `autonomia_fonte` existe justamente para impedir que a Aurora cite WLTP como se fosse o
número brasileiro.

**3. Verificação na saída, antes de a mensagem chegar ao cliente.** Um passo determinístico
extrai todo número com cara de preço, quilometragem ou prazo da resposta gerada e confere contra
os valores que as tools devolveram naquele turno. Divergência não é corrigida em silêncio: a
mensagem é bloqueada, o turno é regenerado uma vez, e a segunda falha vira handoff para humano
com o incidente registrado no trace.

Este passo é o único ponto do sistema onde eu aceito latência extra sem discussão.

## Alternativas consideradas

### Instrução no system prompt ("nunca invente preços") — **descartada**

É a solução óbvia, e é a que não garante nada. Instrução em prompt some no diff: ninguém revisa a
remoção de uma frase de prompt com o rigor com que revisa a remoção de uma função. Ela também
não sobrevive a prompt injection, nem a um turno longo em que a instrução ficou 40 mil tokens
atrás.

Ela continua existindo no prompt — como reforço. O que não existe é depender dela.

### Catálogo inteiro no contexto — **descartada**

Foi tentador quando eu estimava 15 modelos. Com o catálogo real de ~58 modelos
([lista](../pesquisa/MARCAS-E-MODELOS-BR.md)) ele ainda caberia em janela grande, ao custo de
tokens em **todo turno** — o que colide direto com o teto do
[ADR-006](ADR-006-observabilidade-e-teto-de-custo.md).

Mas o custo nem chega a ser o motivo principal. Descartei por dois outros. O primeiro é que o modelo
**parafraseia** o que está no contexto — "R$ 149.990" volta como "cerca de 150 mil" com
frequência incômoda, e "cerca de" numa proposta comercial é um problema jurídico. O segundo é que
estoque muda durante a conversa: o contexto carregado no turno 1 estaria desatualizado no turno
9, exatamente quando o cliente decide.

### Verificar só na proposta, e deixar o chat solto — **descartada**

Foi a alternativa mais difícil de recusar, porque a proposta é o documento que vale e o custo de
verificar cada turno é real.

Recusei porque a expectativa se forma na conversa, não no PDF. Se a Aurora disser "faz uns 400
km" no turno 4 e a proposta trouxer 372, o cliente já decidiu com base no número errado — e
descobre a diferença se sentindo enganado, que é pior do que ter recebido 372 desde o início.
O medo do Raí é sobre a conversa, não sobre o documento.

## Consequências

**Aceitas:**

- Latência maior por turno: uma ida ao banco antes e uma verificação depois. Medida em
  [S-08](../spec/S-08-observabilidade-e-custo.md); orçamento de p95 ≤ 3,5s até o primeiro token.
- Custo maior por conversa: tool calls a mais. Contabilizado no teto de R$ 0,45/conversa.
- A Aurora às vezes soa mais precisa do que uma pessoa soaria ("372 km" onde um vendedor diria
  "uns 370"). O Raí preferiu assim, e o prompt permite arredondar **para baixo** com marcação
  explícita ("mais de 370 km") — nunca para cima.
- Falso positivo do verificador: o cliente diz "tenho 150 mil" e a Aurora repete "150 mil". A
  regra ignora números originados na fala do próprio cliente no mesmo turno.

**Ganhas:**

- O eval de preço vira teste de CI: 40 conversas gravadas, e qualquer preço divergente do banco
  quebra o build ([PRD §6.2](../PRD.md#62-segurança-e-custo--critérios-de-bloqueio)).
- Trocar o modelo de linguagem não coloca preço em risco. A garantia está fora do modelo.
